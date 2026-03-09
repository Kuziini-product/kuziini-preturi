"""
Vercel Cron Job - Pre-cache prices for all products
Runs nightly at 2:00 AM Romania time (23:00 UTC previous day)
Processes products in batches to stay within 60s timeout.
After each batch, triggers next batch via HTTP call (self-chain).
"""
from http.server import BaseHTTPRequestHandler
import json
import time
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from scraper import search_product, load_products, log

CACHE_FILE = '/tmp/prices_cache.json'
BATCH_SIZE = 2  # produse per invocatie (60s timeout pe Vercel Hobby)


def load_cache():
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'prices': {}, 'last_update': None, 'batch_index': 0}


def save_cache(cache):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)


def trigger_next_batch(host):
    """Fire-and-forget: apeleaza urmatorul batch."""
    try:
        url = f'https://{host}/api/cron?chain=1'
        req = urllib.request.Request(url, method='GET')
        req.add_header('User-Agent', 'Vercel-Cron-Chain')
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # fire-and-forget, nu conteaza daca esueaza


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        start = time.time()
        products = load_products()
        all_codes = sorted(products.keys())
        total = len(all_codes)

        if total == 0:
            self._json({'error': 'No products in Excel', 'count': 0})
            return

        cache = load_cache()
        batch_idx = cache.get('batch_index', 0)

        # Parse query params
        parsed = __import__('urllib.parse', fromlist=['parse_qs']).parse_qs(
            __import__('urllib.parse', fromlist=['urlparse']).urlparse(self.path).query
        )
        is_chain = parsed.get('chain', [''])[0] == '1'
        is_reset = parsed.get('reset', [''])[0] == '1'

        # Reset: sterge cache si incepe de la 0
        if is_reset:
            batch_idx = 0
            cache = {'prices': {}, 'last_update': None, 'batch_index': 0}

        # Daca am terminat toate produsele, stop (nu mai chaina)
        if batch_idx >= total:
            cache['batch_index'] = 0  # reset pentru urmatoarea noapte
            cache['completed_at'] = time.time()
            save_cache(cache)
            self._json({
                'ok': True,
                'status': 'COMPLETED',
                'total_cached': cache.get('total_cached', 0),
                'total_products': total,
            })
            return

        # Proceseaza batch curent
        batch_codes = all_codes[batch_idx:batch_idx + BATCH_SIZE]
        results = {}
        for code in batch_codes:
            elapsed = time.time() - start
            if elapsed > 50:  # safety margin (timeout=60s)
                log(f"  CRON: timeout safety, oprit dupa {len(results)} produse")
                break
            try:
                result = search_product(code)
                results[code] = {
                    'prices': result.get('prices', {}),
                    'urls': result.get('urls', {}),
                    'image_url': result.get('image_url'),
                    'category': result.get('category', ''),
                    'kuziini_price': result.get('kuziini_price'),
                    'cached_at': time.time(),
                }
                log(f"  CRON: {code} OK")
            except Exception as e:
                log(f"  CRON: {code} EROARE: {e}")
                results[code] = {'error': str(e), 'cached_at': time.time()}

        # Actualizeaza cache
        if 'prices' not in cache:
            cache['prices'] = {}
        cache['prices'].update(results)
        new_batch_idx = batch_idx + len(results)
        cache['batch_index'] = new_batch_idx
        cache['last_update'] = time.time()
        cache['total_products'] = total
        cache['total_cached'] = len(cache['prices'])
        save_cache(cache)

        elapsed = time.time() - start

        # Trigger next batch daca mai sunt produse
        has_more = new_batch_idx < total
        if has_more:
            host = self.headers.get('Host', '')
            if host:
                trigger_next_batch(host)

        self._json({
            'ok': True,
            'batch': f'{batch_idx}-{new_batch_idx}/{total}',
            'processed': len(results),
            'total_cached': cache['total_cached'],
            'elapsed': round(elapsed, 1),
            'next_batch': new_batch_idx if has_more else 'DONE',
            'status': 'IN_PROGRESS' if has_more else 'COMPLETED',
        })

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.end_headers()
