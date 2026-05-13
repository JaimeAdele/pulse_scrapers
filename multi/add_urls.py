import json

with open('response_all.json') as f:
    data = json.load(f)

parts = data.get('parts', [])
updated = 0
skipped = 0

for part in parts:
    manufacturer = part.get('manufacturer') or {}
    manufacturer_slug = manufacturer.get('slug')
    part_slug = part.get('slug')

    if manufacturer_slug and part_slug:
        part['url'] = f"https://parts.multi-inc.com/part/{manufacturer_slug}/{part_slug}"
        updated += 1
    else:
        skipped += 1
        print(f"Skipped (missing slug): {part.get('part_number', part.get('id'))}")

with open('response_all.json', 'w') as f:
    json.dump(data, f, indent=2)

print(f"Done. Updated: {updated}, Skipped: {skipped}")
