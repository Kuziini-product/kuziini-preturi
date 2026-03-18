#!/usr/bin/env python3
"""
Altex Price Scraper - Playwright (browser headless)
Ruleaza local pe PC, extrage preturile Altex si le salveaza in Redis (Upstash).
Vercel citeste preturile din cache fara a mai accesa altex.ro direct.

Utilizare:
  python altex_scraper.py              # scrape toate produsele
  python altex_scraper.py QE48S90F     # scrape un singur produs (partial match)

Cerinte:
  pip install playwright
  playwright install chromium
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

# Load Redis credentials from .env.local
ENV_FILE = os.path.join(SCRIPT_DIR, '.env.local')
REDIS_URL = ''
REDIS_TOKEN = ''
if os.path.isfile(ENV_FILE):
    with open(ENV_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('UPSTASH_REDIS_REST_URL='):
                REDIS_URL = line.split('=', 1)[1].strip('"').strip("'")
            elif line.startswith('UPSTASH_REDIS_REST_TOKEN='):
                REDIS_TOKEN = line.split('=', 1)[1].strip('"').strip("'")

if not REDIS_URL or not REDIS_TOKEN:
    print("EROARE: Nu am gasit UPSTASH_REDIS_REST_URL/TOKEN in .env.local")
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


def update_altex_price(code, price, url):
    """Update only the Altex price in the existing cache entry."""
    raw = redis_cmd('GET', f'price:{code}')
    if raw:
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            entry = {}
    else:
        entry = {}

    # Update or create entry
    prices = entry.get('prices', {})
    urls = entry.get('urls', {})
    prices['altex'] = price
    urls['altex'] = url
    entry['prices'] = prices
    entry['urls'] = urls
    if 'cached_at' not in entry:
        entry['cached_at'] = time.time()

    payload = json.dumps(entry, ensure_ascii=False)
    redis_cmd('SET', f'price:{code}', payload, 'EX', 176400)  # 49h TTL
    return True


# ─── Product loading ─────────────────────────────────────────────────────────

def load_products():
    with open(PRODUCTS_JSON, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_search_variants(code):
    """Generate search variants for Altex (remove region suffixes)."""
    variants = [code]
    code_up = code.upper()

    if '/' in code_up:
        base = code_up.split('/')[0]
        if base not in variants:
            variants.append(base)
        code_up = base

    # Remove region suffixes
    for pat in [r'(FKXXH)$', r'(TXXH|BTXXH|ATXXH|CTXXH|DTXXH|ETXXH|FTXXH)$',
                r'(AUXXH|BUXXH|CUXXH)$', r'(AXXXH|BXXXH|CXXXH)$',
                r'(EXXN|BXXN|CXXN)$', r'(XXH|XXN|XXU)$']:
        m = re.search(pat, code_up)
        if m:
            base = code_up[:m.start()]
            if base not in variants:
                variants.append(base)
            break

    return variants


# ─── Playwright scraping ─────────────────────────────────────────────────────

def scrape_altex_prices(codes_with_info, headless=True):
    """Scrape Altex prices using Playwright browser."""
    from playwright.sync_api import sync_playwright

    results = {}
    total = len(codes_with_info)

    with sync_playwright() as p:
        # Use real Chrome install to bypass anti-bot detection
        chrome_path = None
        for candidate in [
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            os.path.expanduser(r'~\AppData\Local\Google\Chrome\Application\chrome.exe'),
        ]:
            if os.path.isfile(candidate):
                chrome_path = candidate
                break

        if chrome_path:
            print(f"  Chrome real: {chrome_path}")
            browser = p.chromium.launch(
                headless=headless,
                executable_path=chrome_path,
                args=['--disable-blink-features=AutomationControlled'],
            )
        else:
            print("  Chrome nu a fost gasit, folosesc Chromium bundled")
            browser = p.chromium.launch(
                headless=headless,
                args=['--disable-blink-features=AutomationControlled'],
            )

        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='ro-RO',
        )
        # Remove webdriver flag
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)
        page = context.new_page()

        # Warmup - visit homepage to get cookies
        print("Warmup: visit altex.ro...")
        try:
            page.goto('https://altex.ro/', timeout=30000)
            page.wait_for_load_state('domcontentloaded', timeout=15000)
            time.sleep(2)
            # Accept cookies if popup appears
            try:
                btn = page.locator('button:has-text("Accept"), button:has-text("Accepta"), #onetrust-accept-btn-handler')
                if btn.count() > 0:
                    btn.first.click()
                    time.sleep(1)
            except Exception:
                pass
            print("  OK - cookies set")
        except Exception as e:
            print(f"  Warmup eroare: {e}")

        for idx, (code, info) in enumerate(codes_with_info):
            variants = get_search_variants(code)
            price = None
            found_url = None

            print(f"\n[{idx+1}/{total}] {code} (variante: {variants})")

            for variant in variants[:3]:
                search_url = f'https://altex.ro/cauta/?q={urllib.parse.quote(variant)}'
                print(f"  Cautare: {search_url}")

                try:
                    page.goto(search_url, timeout=30000)
                    page.wait_for_load_state('domcontentloaded', timeout=15000)
                    # Wait for JS to render products (Altex is client-side rendered)
                    time.sleep(5)

                    # Strategy 1: Find product links with /cpd/ and match code
                    try:
                        products_data = page.evaluate('''(searchCode) => {
                            var links = document.querySelectorAll('a[href*="/cpd/"]');
                            var seen = {};
                            var result = [];
                            for (var i = 0; i < links.length; i++) {
                                var a = links[i];
                                var href = a.href;
                                if (seen[href]) continue;
                                seen[href] = true;
                                var card = a.parentElement;
                                for (var d = 0; d < 5; d++) {
                                    if (card.parentElement) card = card.parentElement;
                                }
                                var text = card.textContent.replace(/\\s+/g, ' ').trim();
                                var title = a.textContent.trim();
                                if (!title && a.querySelector('img')) {
                                    title = a.querySelector('img').alt || '';
                                }
                                result.push({href: href, title: title, text: text.substring(0, 500)});
                            }
                            return result;
                        }''', variant)

                        code_lower = code.lower()
                        variant_lower = variant.lower()
                        for prod in products_data[:10]:
                            href_lower = prod['href'].lower()
                            title_lower = prod['title'].lower()
                            text_lower = prod['text'].lower()
                            # Check if the product matches our code
                            matches = False
                            for check in get_search_variants(code):
                                cl = check.lower()
                                if cl in href_lower or cl in title_lower or cl in text_lower:
                                    matches = True
                                    break
                            if not matches:
                                continue
                            # Extract price from card text
                            all_prices_in_card = re.findall(
                                r'([\d.]+,\d{2})\s*lei', prod['text'])
                            parsed = []
                            for raw in all_prices_in_card:
                                clean = re.sub(r'\.(\d{3})', r'\1', raw)
                                clean = clean.replace(',', '.')
                                try:
                                    p = float(clean)
                                    if 50 < p < 300000:
                                        parsed.append(p)
                                except ValueError:
                                    pass
                            if parsed:
                                price = min(parsed)  # sale price (lowest)
                                found_url = prod['href']
                                print(f"    PRET GASIT (produs match): {price} RON | {prod['title'][:60]}")
                                break

                    except Exception as e:
                        print(f"    Product search eroare: {e}")

                    # Strategy 2: Fallback - body text prices (risky, may get wrong product)
                    if not price:
                        try:
                            body_text = page.inner_text('body')
                            # Check if page has only 1 product result
                            count_match = re.search(r'\((\d+)\s*produs', body_text)
                            if count_match and int(count_match.group(1)) == 1:
                                all_prices_text = re.findall(
                                    r'([\d.]+,\d{2})\s*lei', body_text)
                                parsed_prices = []
                                for raw in all_prices_text:
                                    clean = re.sub(r'\.(\d{3})', r'\1', raw)
                                    clean = clean.replace(',', '.')
                                    try:
                                        p = float(clean)
                                        if 50 < p < 300000:
                                            parsed_prices.append(p)
                                    except ValueError:
                                        pass
                                if parsed_prices:
                                    price = min(parsed_prices)
                                    found_url = search_url
                                    print(f"    PRET GASIT (single result): {price} RON")
                        except Exception as e:
                            print(f"    Body text eroare: {e}")

                    # Strategy 2: Extract from __NEXT_DATA__ JSON (if server-rendered)
                    if not price:
                        try:
                            nd_text = page.evaluate('''() => {
                                const el = document.getElementById('__NEXT_DATA__');
                                return el ? el.textContent : null;
                            }''')
                            if nd_text:
                                nd_data = json.loads(nd_text)
                                text = json.dumps(nd_data)
                                price_matches = re.findall(
                                    r'"priceFinal":\s*([\d.]+)', text)
                                if price_matches:
                                    p = float(price_matches[0])
                                    if 10 < p < 300000:
                                        price = p
                                        url_matches = re.findall(
                                            r'"url":\s*"(/[^"]*?/cpd/[^"]*?)"', text)
                                        found_url = ('https://altex.ro' + url_matches[0]) if url_matches else search_url
                                        print(f"    PRET GASIT (__NEXT_DATA__): {price} RON")
                                        break
                        except Exception:
                            pass

                    if price:
                        break

                except Exception as e:
                    print(f"    Eroare pagina: {e}")
                    continue

            if price and found_url:
                results[code] = {'price': price, 'url': found_url}
                # Save to Redis immediately
                try:
                    update_altex_price(code, price, found_url)
                    print(f"    -> Redis OK: {price} RON")
                except Exception as e:
                    print(f"    -> Redis EROARE: {e}")
            else:
                print(f"    -> Nu s-a gasit pret Altex")
                results[code] = {'price': None, 'url': None}

        browser.close()

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

    print(f"=" * 60)
    print(f"Altex Scraper - Playwright")
    print(f"Produse: {len(codes)}")
    print(f"Redis: {REDIS_URL[:40]}...")
    print(f"=" * 60)

    t0 = time.time()
    headless = '--headless' in sys.argv or '-h' in sys.argv
    results = scrape_altex_prices(codes, headless=headless)
    elapsed = time.time() - t0

    # Summary
    found = sum(1 for r in results.values() if r['price'])
    print(f"\n{'=' * 60}")
    print(f"REZULTAT: {found}/{len(results)} preturi gasite")
    print(f"Timp: {elapsed:.0f}s ({elapsed/len(results):.1f}s/produs)")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
