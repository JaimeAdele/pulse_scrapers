import json
import os
import time
import requests
from urllib.parse import urlparse

OUTPUT_DIR = "images"
os.makedirs(OUTPUT_DIR, exist_ok=True)

with open("response_all.json") as f:
    data = json.load(f)

parts = data.get("parts", [])
all_urls = []
for part in parts:
    for url in part.get("images", []):
        all_urls.append((part.get("part_number", part.get("id")), url))

print(f"Found {len(all_urls)} images across {sum(1 for p in parts if p.get('images'))} products\n")

downloaded = 0
skipped = 0
errors = 0

for part_number, url in all_urls:
    asset_id = os.path.basename(urlparse(url).path)
    filename = os.path.join(OUTPUT_DIR, f"{asset_id}.jpg")

    if os.path.exists(filename):
        print(f"[skip] {part_number}: {asset_id}")
        skipped += 1
        continue

    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        with open(filename, "wb") as f:
            f.write(response.content)
        print(f"[ok]   {part_number}: {asset_id}")
        downloaded += 1
        time.sleep(0.2)
    except Exception as e:
        print(f"[err]  {part_number}: {asset_id} — {e}")
        errors += 1

print(f"\nDone. Downloaded: {downloaded}, Skipped: {skipped}, Errors: {errors}")
