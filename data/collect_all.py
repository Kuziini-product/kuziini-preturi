#!/usr/bin/env python3
"""Collect all product data from Kuziini API and save to local JSON archive."""

import json
import time
import urllib.request
import urllib.error
import sys
import os

BASE_URL = "https://kuziini-preturi.vercel.app/api"

def fetch_json(url, retries=2):
    """Fetch JSON from URL with retries."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "KuziiniCollector/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            if attempt < retries:
                time.sleep(2)
            else:
                print(f"  EROARE: {e}", file=sys.stderr)
                return None

def main():
    # 1. Get all product codes
    print("Preiau lista de produse...")
    products_data = fetch_json(f"{BASE_URL}/products?t={int(time.time())}")
    if not products_data:
        print("Nu pot prelua lista de produse!", file=sys.stderr)
        sys.exit(1)

    product_list = products_data.get("products", [])
    print(f"Total produse: {len(product_list)}")

    archive = {}

    for i, prod in enumerate(product_list):
        code = prod["code"]
        print(f"[{i+1}/{len(product_list)}] {code}...", end=" ", flush=True)

        # Fetch search data (prices, URLs, image)
        search = fetch_json(f"{BASE_URL}/search?code={urllib.request.quote(code, safe='')}&t={int(time.time())}")

        # Fetch specs
        specs = fetch_json(f"{BASE_URL}/specs?code={urllib.request.quote(code, safe='')}&t={int(time.time())}")

        entry = {
            "code": code,
            "group": prod.get("group"),
            "category": prod.get("category"),
            "inches": prod.get("inches"),
            "kuziini_price": prod.get("price"),
        }

        if search:
            entry["image_url"] = search.get("image_url")
            entry["prices"] = search.get("prices", {})
            entry["urls"] = search.get("urls", {})
        else:
            entry["image_url"] = None
            entry["prices"] = {}
            entry["urls"] = {}

        if specs and specs.get("specs"):
            entry["specs"] = specs["specs"]
        else:
            entry["specs"] = None

        archive[code] = entry

        # Count vendors with prices
        filled = sum(1 for v in entry["prices"].values() if v is not None)
        print(f"OK ({filled}/4 vendori)", flush=True)

        # Small delay to avoid overwhelming the API
        time.sleep(0.3)

    # Save to JSON
    output_path = os.path.join(os.path.dirname(__file__), "archive_preturi.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(archive, f, indent=2, ensure_ascii=False)

    # Stats
    total = len(archive)
    with_samsung = sum(1 for p in archive.values() if p["prices"].get("samsung"))
    with_emag = sum(1 for p in archive.values() if p["prices"].get("emag"))
    with_flanco = sum(1 for p in archive.values() if p["prices"].get("flanco"))
    with_altex = sum(1 for p in archive.values() if p["prices"].get("altex"))
    with_specs = sum(1 for p in archive.values() if p["specs"])
    with_image = sum(1 for p in archive.values() if p["image_url"] and "placehold" not in p["image_url"])

    print(f"\n{'='*50}")
    print(f"ARHIVA SALVATA: {output_path}")
    print(f"{'='*50}")
    print(f"Total produse: {total}")
    print(f"Samsung preturi: {with_samsung}/{total}")
    print(f"eMAG preturi:    {with_emag}/{total}")
    print(f"Flanco preturi:  {with_flanco}/{total}")
    print(f"Altex preturi:   {with_altex}/{total}")
    print(f"Specificatii:    {with_specs}/{total}")
    print(f"Imagini reale:   {with_image}/{total}")

if __name__ == "__main__":
    main()
