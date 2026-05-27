import json
import os
import time
import msal
import requests
from urllib.parse import urlparse

# ── Configuration ─────────────────────────────────────────────────────────────
CLIENT_ID        = "8cc674ba-0113-4c39-b75f-27c710f33d5c"  # From Azure portal app registration
ONEDRIVE_FOLDER  = "multi-inc-images"     # Destination folder in OneDrive root
TOKEN_CACHE_FILE = "token_cache.json"     # Cached token — keeps you logged in
AUTHORITY        = "https://login.microsoftonline.com/d2a87f9d-dc64-4571-b93b-a7c5482d4460"
SCOPES           = ["https://graph.microsoft.com/Files.ReadWrite"]
INPUT_JSON       = "products.json"
GRAPH_BASE       = "https://graph.microsoft.com/v1.0"
TEST_LIMIT       = None    # set to None to process all images
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
    # First run or refresh failed — device code flow
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
    """Return a set of 'part_number/filename' strings already in OneDrive subfolders."""
    existing = set()
    headers = {"Authorization": f"Bearer {token}"}

    # List subfolders inside the root destination folder
    print("  Fetching subfolder list...", flush=True)
    url = f"{GRAPH_BASE}/me/drive/root:/{ONEDRIVE_FOLDER}:/children?$select=name,folder&$top=999"
    subfolders = []
    while url:
        resp = requests.get(url, headers=headers)
        if resp.status_code == 404:
            print("  Root folder not found — starting fresh.", flush=True)
            return existing
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("value", []):
            if "folder" in item:
                subfolders.append(item["name"])
        url = data.get("@odata.nextLink")
    print(f"  Found {len(subfolders)} subfolders. Scanning for existing files...", flush=True)

    # List files inside each subfolder
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


def ensure_folder_exists(token, part_number, created_folders):
    """Create a subfolder for the part under ONEDRIVE_FOLDER if it doesn't exist yet."""
    if part_number in created_folders:
        return
    url = f"{GRAPH_BASE}/me/drive/root:/{ONEDRIVE_FOLDER}:/children"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "name": part_number,
        "folder": {},
        "@microsoft.graph.conflictBehavior": "fail",
    }
    resp = requests.post(url, headers=headers, json=body)
    if resp.status_code not in (200, 201, 409):  # 409 = already exists, that's fine
        resp.raise_for_status()
    created_folders.add(part_number)


def upload_to_onedrive(token, part_number, filename, image_bytes):
    """Upload image bytes into the part's subfolder in OneDrive."""
    url = f"{GRAPH_BASE}/me/drive/root:/{ONEDRIVE_FOLDER}/{part_number}/{filename}:/content"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "image/jpeg",
    }
    resp = requests.put(url, headers=headers, data=image_bytes)
    resp.raise_for_status()


# ── Main ──────────────────────────────────────────────────────────────────────

with open(INPUT_JSON) as f:
    data = json.load(f)

parts = data.get("parts", [])
test_parts = parts[:TEST_LIMIT] if TEST_LIMIT else parts
all_urls = []
for part in test_parts:
    for url in part.get("images", []):
        all_urls.append((part.get("slug"), url))

print(f"Found {len(all_urls)} images across {len(test_parts)} products{f' (test: first {TEST_LIMIT} items)' if TEST_LIMIT else ''}")

print("\nAuthenticating with OneDrive...")
token = get_token()
print("✓ Authenticated\n")

print("Checking existing files in OneDrive...")
existing = list_existing_files(token)
print(f"✓ {len(existing)} files already in OneDrive\n")

downloaded = 0
skipped = 0
errors = 0
created_folders = set()
total = len(all_urls)

for i, (part_number, url) in enumerate(all_urls, start=1):
    progress = f"[{i}/{total}]"
    asset_id = os.path.basename(urlparse(url).path)
    filename = f"{asset_id}.jpg"

    if f"{part_number}/{filename}" in existing:
        print(f"{progress} [skip] {part_number}/{filename}")
        skipped += 1
        continue

    try:
        # Refresh token on each iteration to handle long runs
        token = get_token()

        # Ensure the part's subfolder exists before uploading
        ensure_folder_exists(token, part_number, created_folders)

        # Download into memory only — never written to disk
        img_resp = requests.get(url, timeout=15)
        img_resp.raise_for_status()
        image_bytes = img_resp.content

        if len(image_bytes) > 4 * 1024 * 1024:
            print(f"{progress} [err]  {part_number}/{filename} — file too large for simple upload (>4MB)")
            errors += 1
            continue

        upload_to_onedrive(token, part_number, filename, image_bytes)
        existing.add(f"{part_number}/{filename}")
        print(f"{progress} [ok]   {part_number}/{filename}")
        downloaded += 1
        time.sleep(0.2)

    except Exception as e:
        print(f"{progress} [err]  {part_number}/{filename} — {e}")
        errors += 1

print(f"\nDone. Downloaded: {downloaded}, Skipped: {skipped}, Errors: {errors}")
