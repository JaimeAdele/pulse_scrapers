import json
import os
import msal
import requests
from urllib.parse import urlparse

# ── Configuration ─────────────────────────────────────────────────────────────
CLIENT_ID        = "8cc674ba-0113-4c39-b75f-27c710f33d5c"
ONEDRIVE_FOLDER  = "multi-inc-images"
TOKEN_CACHE_FILE = "token_cache.json"
AUTHORITY        = "https://login.microsoftonline.com/d2a87f9d-dc64-4571-b93b-a7c5482d4460"
SCOPES           = ["https://graph.microsoft.com/Files.ReadWrite"]
INPUT_JSON       = "products.json"
GRAPH_BASE       = "https://graph.microsoft.com/v1.0"
OUTPUT_JSON      = "missing_images.json"   # set to None to skip saving
# ──────────────────────────────────────────────────────────────────────────────

# Set up MSAL token cache
cache = msal.SerializableTokenCache()
if os.path.exists(TOKEN_CACHE_FILE):
    cache.deserialize(open(TOKEN_CACHE_FILE).read())

app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)


def get_token():
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            if cache.has_state_changed:
                open(TOKEN_CACHE_FILE, "w").write(cache.serialize())
            return result["access_token"]
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "message" not in flow:
        raise RuntimeError(f"Device flow failed: {flow.get('error_description', flow)}")
    print("\n" + flow["message"] + "\n")
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Authentication failed: {result.get('error_description')}")
    if cache.has_state_changed:
        open(TOKEN_CACHE_FILE, "w").write(cache.serialize())
    return result["access_token"]


def list_existing_files(token):
    """Return a set of 'slug/filename' strings already in OneDrive subfolders."""
    existing = set()
    headers = {"Authorization": f"Bearer {token}"}

    print("  Fetching subfolder list...", flush=True)
    url = f"{GRAPH_BASE}/me/drive/root:/{ONEDRIVE_FOLDER}:/children?$select=name,folder&$top=999"
    subfolders = []
    while url:
        resp = requests.get(url, headers=headers)
        if resp.status_code == 404:
            print("  Root folder not found — no images downloaded yet.", flush=True)
            return existing
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("value", []):
            if "folder" in item:
                subfolders.append(item["name"])
        url = data.get("@odata.nextLink")
    print(f"  Found {len(subfolders)} subfolders. Scanning for existing files...", flush=True)

    for idx, subfolder in enumerate(subfolders, start=1):
        print(f"  Scanning subfolder {idx}/{len(subfolders)}: {subfolder}", flush=True)
        url = f"{GRAPH_BASE}/me/drive/root:/{ONEDRIVE_FOLDER}/{subfolder}:/children?$select=name&$top=999"
        while url:
            resp = requests.get(url, headers=headers)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("value", []):
                existing.add(f"{subfolder}/{item['name']}")
            url = data.get("@odata.nextLink")

    return existing


# ── Main ──────────────────────────────────────────────────────────────────────

with open(INPUT_JSON) as f:
    data = json.load(f)

parts = data.get("parts", [])
print(f"Loaded {len(parts)} parts from {INPUT_JSON}")

print("\nAuthenticating with OneDrive...")
token = get_token()
print("✓ Authenticated\n")

print("Checking existing files in OneDrive...")
existing = list_existing_files(token)
print(f"✓ {len(existing)} files already in OneDrive\n")

# Find all missing images
missing = []   # list of dicts, one per missing image
total_images = 0

for part in parts:
    part_id      = part.get("id")
    part_number  = part.get("part_number")
    slug         = part.get("slug")
    images       = part.get("images", [])

    for url in images:
        total_images += 1
        asset_id = os.path.basename(urlparse(url).path)
        filename = f"{asset_id}.jpg"

        if f"{slug}/{filename}" not in existing:
            missing.append({
                "id":          part_id,
                "part_number": part_number,
                "slug":        slug,
                "url":         url,
            })

print(f"Total images in products.json : {total_images}")
print(f"Already in OneDrive           : {total_images - len(missing)}")
print(f"Missing                       : {len(missing)}\n")

if missing:
    print(f"{'ID':<38}  {'Part Number':<20}  {'Slug':<30}  URL")
    print("-" * 120)
    for m in missing:
        print(f"{m['id']:<38}  {m['part_number']:<20}  {m['slug']:<30}  {m['url']}")

if OUTPUT_JSON:
    with open(OUTPUT_JSON, "w") as f:
        json.dump(missing, f, indent=2)
    print(f"\nSaved to {OUTPUT_JSON}")
