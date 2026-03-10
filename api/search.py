"""
Vercel Serverless Function - Kuziini Price Search API
Uses native Vercel Python handler (BaseHTTPRequestHandler)
"""
from http.server import BaseHTTPRequestHandler
import json
import time
import urllib.parse
import sys
import os

# Add parent dir to path so we can import scraper
sys.path.insert(0, os.path.dirname(__file__))
from scraper import search_product, search_single_vendor, load_products, APP_VERSION, warmup_session

# Warmup on cold start
warmup_session()
_start_time = time.time()

CACHE_FILE = '/tmp/prices_cache.json'
CACHE_MAX_AGE = 24 * 3600  # 24 ore


def get_cached_result(code):
    """Returneaza rezultatul din cache daca e valid (< 24h)."""
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        entry = cache.get('prices', {}).get(code.upper())
        if entry and 'cached_at' in entry:
            age = time.time() - entry['cached_at']
            if age < CACHE_MAX_AGE and 'prices' in entry:
                return {
                    'code': code.upper(),
                    'category': entry.get('category', ''),
                    'kuziini_price': entry.get('kuziini_price'),
                    'image_url': entry.get('image_url'),
                    'prices': entry['prices'],
                    'urls': entry.get('urls', {}),
                    'cached': True,
                    'cache_age_min': round(age / 60),
                }
    except Exception:
        pass
    return None


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == '/api/search':
            code = params.get('code', [''])[0].strip().upper()
            vendor = params.get('vendor', [''])[0].strip().lower()
            if not code:
                self._json({'error': 'Codul produsului este gol.'}, 400)
                return

            # Single vendor mode: /api/search?code=X&vendor=samsung
            if vendor:
                result = search_single_vendor(code, vendor)
                self._json(result)
                return

            # Verifica cache-ul
            cached = get_cached_result(code)
            if cached:
                self._json(cached)
                return

            # Daca nu e in cache, returnam info din Excel + mesaj
            import time as _t; _ts = _t.time()
            products = load_products()
            _elapsed = _t.time() - _ts
            kuziini_price = None
            category = ''
            if code in products:
                kuziini_price = products[code]['price']
                category = products[code]['category']

            self._json({
                'code': code,
                'category': category,
                'kuziini_price': round(kuziini_price, 2) if kuziini_price else None,
                'image_url': None,
                'prices': {'samsung': None, 'emag': None, 'flanco': None, 'altex': None},
                'urls': {},
                'not_cached': True,
                'message': 'Prețurile nu sunt încă disponibile. Se actualizează automat.',
                'excel_load_ms': round(_elapsed * 1000),
            })

        elif path == '/api/version':
            self._json({'version': _start_time, 'app_version': APP_VERSION})

        elif path == '/api/reload_excel':
            import scraper as scraper_mod
            scraper_mod._products_cache = None
            products = load_products()
            self._json({'ok': True, 'count': len(products)})

        elif path == '/api/ping':
            self._json({'pong': True, 'time': time.time()})

        elif path == '/api/test_excel':
            t0 = time.time()
            products = load_products()
            t1 = time.time()
            self._json({
                'ok': True,
                'count': len(products),
                'elapsed_ms': round((t1 - t0) * 1000),
                'codes_sample': list(products.keys())[:5],
            })

        elif path == '/api/cache_status':
            try:
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                last = cache.get('last_update')
                age = round((time.time() - last) / 60) if last else None
                self._json({
                    'total_cached': cache.get('total_cached', 0),
                    'total_products': cache.get('total_products', 0),
                    'last_update_min_ago': age,
                    'batch_index': cache.get('batch_index', 0),
                })
            except Exception:
                self._json({'total_cached': 0, 'error': 'No cache file'})

        else:
            self._json({'error': 'Not found'}, 404)

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
