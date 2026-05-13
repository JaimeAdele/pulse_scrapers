import json

with open('response_all.json') as f:
    data = json.load(f)

parts = data.get('parts', [])
total_images = sum(len(p.get('images', [])) for p in parts)
parts_with_images = sum(1 for p in parts if p.get('images'))

print(f"Total image URLs: {total_images}")
print(f"Products with images: {parts_with_images} / {len(parts)}")
