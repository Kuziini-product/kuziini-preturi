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
from scraper import search_product, load_products, log, set_cron_timeouts, get_samsung_specs

# Cron are 60s budget, mareste timeout-urile
set_cron_timeouts()
from cache import (
    set_cached_price, get_cache_status, set_cache_status, is_configured,
    save_price_history
)

BATCH_SIZE = 1  # 1 produs per invocatie (scraping dureaza ~30-50s per produs)


def trigger_next_batch(host):
    """Fire-and-forget: apeleaza urmatorul batch."""
    try:
        url = f'https://{host}/api/cron?chain=1'
        req = urllib.request.Request(url, method='GET')
        req.add_header('User-Agent', 'Vercel-Cron-Chain')
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # fire-and-forget


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not is_configured():
            self._json({'error': 'Redis not configured. Set UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN.'}, 500)
            return

        start = time.time()
        products = load_products()
        all_codes = sorted(products.keys())
        total = len(all_codes)

        if total == 0:
            self._json({'error': 'No products in Excel', 'count': 0})
            return

        status = get_cache_status()
        batch_idx = status.get('batch_index', 0)

        # Parse query params
        parsed = __import__('urllib.parse', fromlist=['parse_qs']).parse_qs(
            __import__('urllib.parse', fromlist=['urlparse']).urlparse(self.path).query
        )
        is_chain = parsed.get('chain', [''])[0] == '1'
        is_reset = parsed.get('reset', [''])[0] == '1'

        # Reset: incepe de la 0
        if is_reset:
            batch_idx = 0
            set_cache_status({
                'total_cached': 0,
                'total_products': total,
                'last_update': None,
                'batch_index': 0,
            })

        # Daca am terminat toate produsele, stop
        if batch_idx >= total:
            status['batch_index'] = 0  # reset pentru urmatoarea noapte
            status['completed_at'] = time.time()
            set_cache_status(status)
            self._json({
                'ok': True,
                'status': 'COMPLETED',
                'total_cached': status.get('total_cached', 0),
                'total_products': total,
            })
            return

        # Proceseaza batch curent
        batch_codes = all_codes[batch_idx:batch_idx + BATCH_SIZE]
        processed = 0
        for code in batch_codes:
            elapsed = time.time() - start
            if elapsed > 50:  # safety margin (timeout=60s)
                log(f"  CRON: timeout safety, oprit dupa {processed} produse")
                break
            try:
                result = search_product(code, cron_mode=True)
                # Salveaza in Redis
                set_cached_price(code, result)
                # Salveaza istoricul preturilor (snapshot zilnic)
                if result.get('prices'):
                    save_price_history(code, result['prices'])
                # Pre-cache specificatii Samsung (nu incape in 10s user request)
                elapsed2 = time.time() - start
                if elapsed2 < 40:  # doar daca avem timp
                    try:
                        from cache import _redis_cmd
                        import json as _json
                        specs = get_samsung_specs(code)
                        if specs:
                            payload = _json.dumps(specs, ensure_ascii=False)
                            _redis_cmd('SET', f'specs:{code}', payload, 'EX', 604800)
                            log(f"  CRON: {code} specs cached")
                    except Exception as se:
                        log(f"  CRON: {code} specs EROARE: {se}")
                processed += 1
                log(f"  CRON: {code} OK")
            except Exception as e:
                log(f"  CRON: {code} EROARE: {e}")
                processed += 1  # skip si treci mai departe

        # Actualizeaza status
        new_batch_idx = batch_idx + processed
        status['batch_index'] = new_batch_idx
        status['last_update'] = time.time()
        status['total_products'] = total
        status['total_cached'] = new_batch_idx
        set_cache_status(status)

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
            'processed': processed,
            'total_cached': new_batch_idx,
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
