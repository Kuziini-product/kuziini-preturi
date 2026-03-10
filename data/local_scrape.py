#!/usr/bin/env python3
"""
Local full scraping - ruleaza scraperele cu timeout-uri mari, fara limita Vercel de 60s.
Salveaza tot in archive_preturi.json.
"""
import json
import os
import sys
import time

# Adauga api/ in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

# Import inainte de a seta timeout-uri
import scraper

# Override timeout-uri pentru local (fara limita de 60s)
scraper.CURL_TIMEOUT = 25
scraper.REQ_TIMEOUT = 20

from scraper import (
    scrape_samsung, scrape_emag, scrape_flanco, scrape_altex,
    get_samsung_specs, get_product_image, load_products,
    get_search_variants, log
)

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_FILE = os.path.join(DATA_DIR, 'archive_preturi.json')

def load_existing_archive():
    if os.path.exists(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_archive(archive):
    with open(ARCHIVE_FILE, 'w', encoding='utf-8') as f:
        json.dump(archive, f, indent=2, ensure_ascii=False)

def main():
    # Load existing archive to preserve data
    archive = load_existing_archive()

    # Load products
    products = load_products()
    if not products:
        # Fallback: load from products.json
        pj = os.path.join(DATA_DIR, 'products.json')
        with open(pj, 'r', encoding='utf-8') as f:
            products = json.load(f)

    # Build product list
    if isinstance(products, dict):
        product_list = list(products.values())
    else:
        product_list = products

    total = len(product_list)
    print(f"Total produse: {total}")

    # Parse command line for selective scraping
    only_vendors = None
    start_idx = 0
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if arg.startswith('--vendor='):
                only_vendors = arg.split('=')[1].split(',')
            elif arg.startswith('--start='):
                start_idx = int(arg.split('=')[1])
            elif arg == '--missing':
                # Only scrape products with missing vendor prices
                pass

    stats = {'samsung': 0, 'emag': 0, 'flanco': 0, 'altex': 0, 'specs': 0, 'images': 0}

    for i, prod in enumerate(product_list):
        if i < start_idx:
            continue

        code = prod.get('code', '') if isinstance(prod, dict) else prod
        group = prod.get('group', 'TV') if isinstance(prod, dict) else 'TV'
        category = prod.get('category', '') if isinstance(prod, dict) else ''
        inches = prod.get('inches') if isinstance(prod, dict) else None
        kuziini_price = prod.get('price') if isinstance(prod, dict) else None

        print(f"\n{'='*60}")
        print(f"[{i+1}/{total}] {code} ({category})")
        print(f"{'='*60}")

        # Get existing data
        existing = archive.get(code, {})
        existing_prices = existing.get('prices', {})

        entry = {
            'code': code,
            'group': group,
            'category': category,
            'inches': inches,
            'kuziini_price': kuziini_price,
            'prices': {},
            'urls': {},
            'specs': existing.get('specs'),
            'image_url': existing.get('image_url'),
        }

        vendors = {
            'samsung': scrape_samsung,
            'emag': scrape_emag,
            'flanco': scrape_flanco,
            'altex': scrape_altex,
        }

        for vendor_name, scrape_fn in vendors.items():
            if only_vendors and vendor_name not in only_vendors:
                # Keep existing
                entry['prices'][vendor_name] = existing_prices.get(vendor_name)
                entry['urls'][vendor_name] = existing.get('urls', {}).get(vendor_name)
                continue

            # Skip if already have price (unless forced)
            if existing_prices.get(vendor_name) and '--force' not in sys.argv:
                entry['prices'][vendor_name] = existing_prices[vendor_name]
                entry['urls'][vendor_name] = existing.get('urls', {}).get(vendor_name)
                print(f"  {vendor_name}: {existing_prices[vendor_name]} RON (cached)")
                stats[vendor_name] += 1
                continue

            try:
                t0 = time.time()
                price, url = scrape_fn(code)
                elapsed = time.time() - t0
                entry['prices'][vendor_name] = price
                entry['urls'][vendor_name] = url
                if price:
                    print(f"  {vendor_name}: {price} RON ({elapsed:.1f}s)")
                    stats[vendor_name] += 1
                else:
                    print(f"  {vendor_name}: - ({elapsed:.1f}s)")
            except Exception as e:
                print(f"  {vendor_name}: EROARE {e}")
                entry['prices'][vendor_name] = existing_prices.get(vendor_name)
                entry['urls'][vendor_name] = existing.get('urls', {}).get(vendor_name)

        # Image
        if not entry['image_url'] or 'placehold' in (entry['image_url'] or ''):
            try:
                img = get_product_image(code)
                if img:
                    entry['image_url'] = img
                    stats['images'] += 1
            except Exception:
                pass
        else:
            if 'placehold' not in (entry['image_url'] or ''):
                stats['images'] += 1

        # Specs (only for TV/Audio, skip if already have)
        if not entry.get('specs'):
            try:
                t0 = time.time()
                specs = get_samsung_specs(code)
                elapsed = time.time() - t0
                if specs:
                    entry['specs'] = specs
                    stats['specs'] += 1
                    print(f"  specs: {len(specs)} sectiuni ({elapsed:.1f}s)")
                else:
                    print(f"  specs: - ({elapsed:.1f}s)")
            except Exception as e:
                print(f"  specs: EROARE {e}")
        else:
            stats['specs'] += 1
            print(f"  specs: cached ({len(entry['specs'])} sectiuni)")

        archive[code] = entry

        # Save after each product (in case of crash)
        save_archive(archive)

        # Progress
        filled = sum(1 for v in entry['prices'].values() if v)
        print(f"  TOTAL: {filled}/4 vendori")

    # Final stats
    print(f"\n{'='*60}")
    print(f"SCRAPING COMPLET!")
    print(f"{'='*60}")
    print(f"Samsung: {stats['samsung']}/{total}")
    print(f"eMAG:    {stats['emag']}/{total}")
    print(f"Flanco:  {stats['flanco']}/{total}")
    print(f"Altex:   {stats['altex']}/{total}")
    print(f"Specs:   {stats['specs']}/{total}")
    print(f"Imagini: {stats['images']}/{total}")
    print(f"\nArhiva: {ARCHIVE_FILE}")

if __name__ == '__main__':
    main()
