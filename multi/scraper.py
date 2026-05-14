#!/usr/bin/env python3
"""
Parts Catalog Scraper for parts.multi-inc.com

This script scrapes product pages from the MULTI, INC. parts catalog
and extracts product data including titles, specifications, and image URLs.

Usage:
    python3 scraper.py [--batch-size N] [--start-index N]

Features:
    - Extracts product titles, modality, condition, lead times
    - Captures technical details (part numbers, system models, etc.)
    - Extracts all product image URLs
    - Handles products with varying data completeness
    - Saves progress in batches
    - Resumes from last completed item
"""

import json
import re
import time
from datetime import datetime
from typing import Dict, Any, List
from pathlib import Path
import argparse
import requests
from bs4 import BeautifulSoup, Tag


class ProductDataParser:
    """Parses product page text data into structured format"""

    @staticmethod
    def parse_page_text(text: str, soup_element=None) -> Dict[str, Any]:
        """Parse product page HTML and extract all available data"""
        data = {}
        details = {}
        attributes = {}

        # Extract title (CT TUBE products)
        title_match = re.search(
            r'(CT TUBE[^:]*?(?:MAXIRAY|SOLARIX|PERFORMIX|MX\d+|CTX)[^:]*?)(?:Manufacturer|Modality|Condition|$)',
            text, re.IGNORECASE
        )
        if title_match:
            data['title'] = title_match.group(1).strip()

        basic_fields = {'modality', 'condition', 'returnable', 'lead_times', 'manufacturer', 'part_number'}

        if soup_element:
            # Extract product details from div.product-content using:
            # <strong>{key}</strong> {value}<br />
            # List values appear as: <strong>{key}</strong><ul><li>...</li></ul>
            product_content = soup_element.find('div', class_='product-content')
            if product_content:
                h3 = product_content.find('h3')
                if h3:
                    data['subheading'] = h3.get_text(strip=True)

                for strong in product_content.find_all('strong'):
                    key = strong.get_text(strip=True).rstrip(':').strip()
                    if not key:
                        continue

                    value_parts = []
                    is_list = False
                    sibling = strong.next_sibling

                    while sibling:
                        if isinstance(sibling, Tag):
                            if sibling.name == 'br':
                                break
                            elif sibling.name == 'ul':
                                items = [li.get_text(strip=True) for li in sibling.find_all('li')]
                                if items:
                                    value_parts = items
                                    is_list = True
                                break
                            elif sibling.name == 'strong':
                                break
                            elif sibling.find('li'):
                                # div or other container wrapping one or more <ul> lists
                                items = [li.get_text(strip=True) for li in sibling.find_all('li')]
                                if items:
                                    value_parts = items
                                    is_list = True
                                break
                        else:
                            txt = str(sibling).strip()
                            if txt:
                                value_parts.append(txt)
                        sibling = sibling.next_sibling

                    value = value_parts if is_list else ' '.join(value_parts).strip()

                    if not value:
                        continue

                    lookup_key = re.sub(r'[^\w\s]', '', key.lower())
                    lookup_key = '_'.join(lookup_key.split())

                    if lookup_key in basic_fields:
                        if lookup_key == 'returnable':
                            returnable_str = value if isinstance(value, str) else ' '.join(value)
                            data['returnable'] = returnable_str.lower() != 'no'
                        else:
                            data[lookup_key] = value if isinstance(value, str) else ' '.join(value)
                    else:
                        if isinstance(value, list):
                            cleaned = [re.sub(r'[®™]', '', v).strip() for v in value]
                            cleaned = [v for v in cleaned if len(v) > 2 and v.lower() not in ['and', 'the', 'or', 'for']]
                            if cleaned:
                                details[key] = cleaned
                        else:
                            details[key] = value

            # Extract image URLs from .gallery-wrapper
            gallery = soup_element.find('div', class_='gallery-wrapper')
            if gallery:
                images = [img['src'] for img in gallery.find_all('img') if img.get('src') and 'width=600' in img['src']]
                if images:
                    data['images'] = images

            # Extract attributes from:
            # <div class="... divide-default ..." data-v-9b0c3b08>
            #   <div ...><span class="text-center ..." data-v-9b0c3b08>{key}</span></div>
            #   <div ...><div class="text-center text-xl font-bold" data-v-9b0c3b08>{value}</div></div>
            # </div>
            attr_containers = soup_element.find_all(
                'div', class_=lambda c: c and 'divide-default' in c
            )
            for container in attr_containers:
                child_divs = container.find_all('div', recursive=False)
                if len(child_divs) >= 2:
                    key_span = child_divs[0].find('span')
                    value_div = child_divs[1].find('div')

                    field_name = key_span.get_text(strip=True) if key_span else None
                    attr_value = value_div.get_text(strip=True) if value_div else None

                    if field_name and attr_value and len(attr_value) > 1:
                        attributes[field_name] = attr_value

        # Remove any details keys that are also in attributes (attributes take precedence)
        if attributes and details:
            for key in attributes.keys():
                details.pop(key, None)

        if details:
            data['details'] = details
        if attributes:
            data['attributes'] = attributes

        return data


class CatalogScraper:
    """Main scraper class for MULTI, INC. parts catalog"""

    def __init__(self, json_file: str = 'products.json'):
        self.json_file = json_file
        self.data = None
        self.parts = []
        self.load_json()

    def load_json(self):
        """Load the products.json file"""
        with open(self.json_file, 'r') as f:
            self.data = json.load(f)
        self.parts = self.data.get('parts', [])
        print(f"✓ Loaded {len(self.parts):,} items from {self.json_file}")

    def save_json(self):
        """Save the updated JSON file"""
        with open(self.json_file, 'w') as f:
            json.dump(self.data, f, indent=2)

    def get_unscraped_items(self) -> List[Dict[str, Any]]:
        """Get list of items that haven't been scraped yet"""
        return [p for p in self.parts if not p.get('scraped')]

    def get_progress(self) -> Dict[str, int]:
        """Get scraping progress statistics"""
        total = len(self.parts)
        scraped = sum(1 for p in self.parts if p.get('scraped'))
        remaining = total - scraped

        return {
            'total': total,
            'scraped': scraped,
            'remaining': remaining,
            'percent': (scraped / total * 100) if total > 0 else 0
        }

    def print_progress(self):
        """Print current progress"""
        progress = self.get_progress()
        print("\n" + "=" * 70)
        print("SCRAPING PROGRESS")
        print("=" * 70)
        print(f"Total items:     {progress['total']:,}")
        print(f"Scraped:         {progress['scraped']:,}")
        print(f"Remaining:       {progress['remaining']:,}")
        print(f"Progress:        {progress['percent']:.2f}%")
        print("=" * 70)

    def update_item(self, item: Dict[str, Any], parsed_data: Dict[str, Any]):
        """Update an item with scraped data"""
        images = parsed_data.pop('images', None)
        item.update(parsed_data)
        if images is not None:
            item['images'] = images
        item['scraped'] = True

    def get_next_batch(self, batch_size: int = 100):
        """Get the next batch of unscraped items"""
        unscraped = self.get_unscraped_items()
        return unscraped[:batch_size]

    def print_item_summary(self, item: Dict[str, Any], index: int):
        """Print summary of a scraped item"""
        part_num = item.get('part_number', 'N/A')
        title = item.get('title', 'N/A')
        images = len(item.get('images', []))

        print(f"\n[{index}] {part_num}")
        print(f"    Title: {title}")
        print(f"    Images: {images}")
        if item.get('details'):
            print(f"    Details: {len(item.get('details', {}))} fields")
        if item.get('attributes'):
            print(f"    Attributes: {len(item.get('attributes', {}))} fields")


ERROR_LOG_FILE = 'scrape_errors.json'

def log_error(item: Dict[str, Any], reason: str):
    """Append a failed item to the error log file"""
    entry = {
        'id': item.get('id'),
        'part_number': item.get('part_number'),
        'url': item.get('url'),
        'error': reason,
        'timestamp': datetime.utcnow().isoformat()
    }
    try:
        with open(ERROR_LOG_FILE, 'r') as f:
            errors = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        errors = []
    errors.append(entry)
    with open(ERROR_LOG_FILE, 'w') as f:
        json.dump(errors, f, indent=2)


def scrape_items(scraper: CatalogScraper, batch_size: int = 100, verbose: bool = False):
    """Scrape a batch of unscraped items"""

    # Get next batch of unscraped items
    batch = scraper.get_next_batch(batch_size)

    if not batch:
        print("\n✓ All items have been scraped!")
        return 0

    print(f"\n{'='*70}")
    print(f"SCRAPING BATCH: {len(batch)} items")
    print(f"{'='*70}\n")

    success_count = 0
    error_count = 0

    for idx, item in enumerate(batch, 1):
        url = item.get('url')
        part_number = item.get('part_number', 'Unknown')

        if not url:
            print(f"[{idx}/{len(batch)}] ✗ {part_number}: No URL found")
            log_error(item, "No URL found")
            error_count += 1
            continue

        try:
            if verbose:
                print(f"[{idx}/{len(batch)}] Fetching: {url}")
            else:
                print(f"[{idx}/{len(batch)}] Scraping: {part_number}...", end=' ', flush=True)

            # Fetch the page, retrying once on network error
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
            except requests.RequestException:
                print(f"retrying...", end=' ', flush=True)
                time.sleep(2)
                response = requests.get(url, timeout=10)
                response.raise_for_status()

            # Parse HTML
            soup = BeautifulSoup(response.content, 'html.parser')
            main_element = soup.find('main')

            if not main_element:
                if not verbose:
                    print("✗ No main element")
                else:
                    print(f"  ✗ Error: No <main> element found")
                log_error(item, "No <main> element found")
                error_count += 1
                continue

            # Extract and parse content
            page_text = main_element.get_text(separator='\n')
            parsed_data = ProductDataParser.parse_page_text(page_text, main_element)

            if not item.get('primary_image'):
                parsed_data.pop('images', None)

            # Update item with parsed data
            scraper.update_item(item, parsed_data)

            if not verbose:
                print("✓")
            else:
                print(f"  ✓ Scraped successfully")
                if parsed_data.get('title'):
                    print(f"    Title: {parsed_data['title']}")
                if parsed_data.get('details'):
                    print(f"    Details: {len(parsed_data['details'])} fields")
                if parsed_data.get('attributes'):
                    print(f"    Attributes: {len(parsed_data['attributes'])} fields")

            success_count += 1

            # Save periodically every 100 products
            if success_count % 100 == 0:
                print(f"\n  [Auto-save after {success_count} products]")
                scraper.save_json()

            # Rate limiting: 0.5 second delay between requests
            if idx < len(batch):  # Don't delay after last item
                time.sleep(0.5)

        except requests.RequestException as e:
            if not verbose:
                print(f"✗ Network error")
            else:
                print(f"  ✗ Network error: {e}")
            log_error(item, f"Network error: {e}")
            error_count += 1

        except Exception as e:
            if not verbose:
                print(f"✗ Error")
            else:
                print(f"  ✗ Parsing error: {e}")
            log_error(item, f"Parsing error: {e}")
            error_count += 1

    # Save progress
    print(f"\n{'='*70}")
    print("Saving progress...")
    scraper.save_json()
    print("✓ Progress saved")

    # Print summary
    print(f"\n{'='*70}")
    print("BATCH SUMMARY")
    print(f"{'='*70}")
    print(f"Total processed:  {len(batch)}")
    print(f"Successful:       {success_count}")
    print(f"Errors:           {error_count}")
    print(f"{'='*70}")

    return success_count


def test_parser(url: str = None, verbose: bool = False):
    """Test the parser with a sample product page"""

    # Initialize scraper to get a sample URL if none provided
    if not url:
        scraper = CatalogScraper()
        unscraped = scraper.get_unscraped_items()
        if not unscraped:
            print("No unscraped items found. Using first item from catalog.")
            url = scraper.parts[0].get('url')
        else:
            url = unscraped[0].get('url')

    print(f"\nTesting parser with URL: {url}")
    print("=" * 70)

    try:
        # Fetch the page
        print("\n[1/3] Fetching page...")
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        print(f"✓ Page fetched successfully ({len(response.content):,} bytes)")

        # Extract text
        print("\n[2/3] Extracting text content...")
        soup = BeautifulSoup(response.content, 'html.parser')
        main_element = soup.find('main')
        if not main_element:
            print("✗ Error: No <main> element found on page")
            return

        page_text = main_element.get_text(separator='\n')
        print(f"✓ Text extracted ({len(page_text):,} characters)")

        # Debug: show first 500 chars of extracted text
        if verbose:
            print(f"\nExtracted text preview:\n{page_text[:500]}...\n")

        # Parse the text
        print("\n[3/3] Parsing product data...")
        parsed_data = ProductDataParser.parse_page_text(page_text, main_element)
        print(f"✓ Parsing complete")

        # Display results
        print("\n" + "=" * 70)
        print("PARSED RESULTS")
        print("=" * 70)

        if not parsed_data:
            print("⚠ No data extracted - parser may need adjustment")
            return

        # Pretty print the results
        print(json.dumps(parsed_data, indent=2))

        # Summary
        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"Fields extracted: {len(parsed_data)}")
        if 'details' in parsed_data:
            print(f"  - Details: {len(parsed_data['details'])} fields")
        if 'attributes' in parsed_data:
            print(f"  - Attributes: {len(parsed_data['attributes'])} fields")
        if 'images' in parsed_data:
            print(f"  - Images: {len(parsed_data.get('images', []))} URLs")
        print("=" * 70)

    except requests.RequestException as e:
        print(f"\n✗ Error fetching page: {e}")
    except Exception as e:
        print(f"\n✗ Error: {e}")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Scrape MULTI, INC. parts catalog'
    )
    parser.add_argument(
        '--batch-size', type=int, default=100,
        help='Number of items to process per batch (default: 100)'
    )
    parser.add_argument(
        '--start-index', type=int, default=0,
        help='Start from specific item index (default: 0)'
    )
    parser.add_argument(
        '--verbose', '-v', action='store_true',
        help='Print detailed progress'
    )
    parser.add_argument(
        '--test', action='store_true',
        help='Run in test mode to verify parser functionality'
    )
    parser.add_argument(
        '--test-url', type=str, default=None,
        help='Specific product URL to test (optional)'
    )

    args = parser.parse_args()

    # If test mode, run test and exit
    if args.test:
        test_parser(args.test_url, args.verbose)
        return

    # Initialize scraper
    scraper = CatalogScraper()

    # Print current progress
    scraper.print_progress()

    # Scrape items
    success_count = scrape_items(scraper, args.batch_size, args.verbose)

    # Print final progress
    print()
    scraper.print_progress()

    if success_count > 0:
        print(f"\n✓ Successfully scraped {success_count} items")
    else:
        print("\n⚠ No items were scraped")


if __name__ == '__main__':
    main()
