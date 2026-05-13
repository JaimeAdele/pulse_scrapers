import json
import sys

filename = sys.argv[1] if len(sys.argv) > 1 else 'products.json'

with open(filename) as f:
    data = json.load(f)

parts = data.get('parts', [])
scraped = sum(1 for p in parts if p.get('scraped'))

print(f"Scraped: {scraped} / {len(parts)}")
print(f"Remaining: {len(parts) - scraped}")
