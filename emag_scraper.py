#!/usr/bin/env python3
"""
eMAG Price Scraper - Requests (fara Playwright)
Ruleaza local pe PC, extrage preturile eMAG si le salveaza in Redis (Upstash).
Vercel citeste preturile din cache/arhiva fara a mai accesa emag.ro direct.

Utilizare:
  python emag_scraper.py              # scrape toate produsele
  python emag_scraper.py QE48S90F     # scrape un singur produs (partial match)

Cerinte:
  pip install requests beautifulsoup4
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# ─── Config ──────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_JSON = os.path.join(SCRIPT_DIR, 'data', 'products.json')

# Load Redis credentials from env vars (GitHub Actions) or .env.local (local)
REDIS_URL = os.environ.get('UPSTASH_REDIS_REST_URL', '')
REDIS_TOKEN = os.environ.get('UPSTASH_REDIS_REST_TOKEN', '')

if not REDIS_URL or not REDIS_TOKEN:
    ENV_FILE = os.path.join(SCRIPT_DIR, '.env.local')
    if os.path.isfile(ENV_FILE):
        with open(ENV_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('UPSTASH_REDIS_REST_URL='):
                    REDIS_URL = line.split('=', 1)[1].strip('"').strip("'")
                elif line.startswith('UPSTASH_REDIS_REST_TOKEN='):
                    REDIS_TOKEN = line.split('=', 1)[1].strip('"').strip("'")

if not REDIS_URL or not REDIS_TOKEN:
    print("EROARE: Nu am gasit UPSTASH_REDIS_REST_URL/TOKEN (nici env vars, nici .env.local)")
    sys.exit(1)


# ─── Redis helpers ───────────────────────────────────────────────────────────

def redis_cmd(*args):
    """Execute Redis command via Upstash REST API."""
    data = json.dumps(list(args)).encode('utf-8')
    req = urllib.request.Request(REDIS_URL, data=data, method='POST')
    req.add_header('Authorization', f'Bearer {REDIS_TOKEN}')
    req.add_header('Content-Type', 'application/json')
    resp = urllib.request.urlopen(req, timeout=10)
    result = json.loads(resp.read().decode('utf-8'))
    return result.get('result')


def update_emag_price(code, price, url):
    """Update the eMAG price in cache entry AND in permanent archive."""
    # 1. Update cache (price:CODE with TTL)
    raw = redis_cmd('GET', f'price:{code}')
    if raw:
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            entry = {}
    else:
        entry = {}

    prices = entry.get('prices', {})
    urls = entry.get('urls', {})
    prices['emag'] = price
    urls['emag'] = url
    entry['prices'] = prices
    entry['urls'] = urls
    if 'cached_at' not in entry:
        entry['cached_at'] = time.time()

    payload = json.dumps(entry, ensure_ascii=False)
    redis_cmd('SET', f'price:{code}', payload, 'EX', 176400)  # 49h TTL

    # 2. Update permanent archive (archive:prices HASH)
    arch_raw = redis_cmd('HGET', 'archive:prices', code)
    if arch_raw:
        try:
            arch = json.loads(arch_raw)
        except (json.JSONDecodeError, TypeError):
            arch = {}
    else:
        arch = {}

    vendors = arch.get('vendors', {})
    vendors['emag'] = {'price': price, 'url': url}
    arch['vendors'] = vendors
    arch['updated'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    arch_payload = json.dumps(arch, ensure_ascii=False)
    redis_cmd('HSET', 'archive:prices', code, arch_payload)

    return True


# ─── Product loading ─────────────────────────────────────────────────────────

def load_products():
    with open(PRODUCTS_JSON, 'r', encoding='utf-8') as f:
        return json.load(f)


# ─── eMAG scraping (reuses api/scraper.py logic) ─────────────────────────────

def scrape_emag_prices(codes_with_info):
    """Scrape eMAG prices using Python requests (works from local IP)."""
    # Import the scraper module
    sys.path.insert(0, os.path.join(SCRIPT_DIR, 'api'))
    from scraper import scrape_emag

    results = {}
    total = len(codes_with_info)

    for idx, (code, info) in enumerate(codes_with_info):
        print(f"\n[{idx+1}/{total}] {code}")
        t0 = time.time()

        try:
            result = scrape_emag(code)
            price = result[0] if result else None
            url = result[1] if result else None
            elapsed = time.time() - t0

            if price and url:
                results[code] = {'price': round(price, 2), 'url': url}
                # Save to Redis immediately
                try:
                    update_emag_price(code, round(price, 2), url)
                    print(f"  -> GASIT: {price:.2f} RON ({elapsed:.1f}s) -> Redis OK")
                except Exception as e:
                    print(f"  -> GASIT: {price:.2f} RON ({elapsed:.1f}s) -> Redis EROARE: {e}")
            else:
                print(f"  -> Nu s-a gasit pret eMAG ({elapsed:.1f}s)")
                results[code] = {'price': None, 'url': None}
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  -> EROARE ({elapsed:.1f}s): {e}")
            results[code] = {'price': None, 'url': None}

        # Small delay to be polite
        if idx < total - 1:
            time.sleep(1)

    return results


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    products = load_products()
    filter_code = sys.argv[1].upper() if len(sys.argv) > 1 else None

    if filter_code:
        codes = [(c, info) for c, info in products.items()
                 if filter_code in c.upper()]
        if not codes:
            print(f"Nu am gasit produse cu codul '{filter_code}'")
            sys.exit(1)
    else:
        codes = list(products.items())

    print(f"{'=' * 60}")
    print(f"eMAG Scraper - Local (requests)")
    print(f"Produse: {len(codes)}")
    print(f"Redis: {REDIS_URL[:40]}...")
    print(f"{'=' * 60}")

    t0 = time.time()
    results = scrape_emag_prices(codes)
    elapsed = time.time() - t0

    # Summary
    found = sum(1 for r in results.values() if r['price'])
    print(f"\n{'=' * 60}")
    print(f"REZULTAT: {found}/{len(results)} preturi eMAG gasite")
    print(f"Timp: {elapsed:.0f}s ({elapsed/len(results):.1f}s/produs)" if results else "Timp: 0s")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
