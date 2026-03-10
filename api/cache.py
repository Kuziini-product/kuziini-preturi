"""
Persistent cache using Upstash Redis REST API.
No pip packages needed - uses urllib only.
Free tier: 10,000 commands/day, 256MB storage.

Environment variables required:
  UPSTASH_REDIS_REST_URL   - e.g. https://xyz.upstash.io
  UPSTASH_REDIS_REST_TOKEN - Bearer token
"""
import json
import os
import time
import urllib.request
import urllib.error

REDIS_URL = os.environ.get('UPSTASH_REDIS_REST_URL', '')
REDIS_TOKEN = os.environ.get('UPSTASH_REDIS_REST_TOKEN', '')

CACHE_MAX_AGE = 24 * 3600  # 24 ore


def _redis_cmd(*args):
    """Execute a Redis command via Upstash REST API."""
    if not REDIS_URL or not REDIS_TOKEN:
        return None
    try:
        data = json.dumps(list(args)).encode('utf-8')
        req = urllib.request.Request(REDIS_URL, data=data, method='POST')
        req.add_header('Authorization', f'Bearer {REDIS_TOKEN}')
        req.add_header('Content-Type', 'application/json')
        resp = urllib.request.urlopen(req, timeout=5)
        result = json.loads(resp.read().decode('utf-8'))
        return result.get('result')
    except Exception as e:
        print(f"  Redis error: {e}")
        return None


def get_cached_price(code):
    """Get cached price data for a product code. Returns dict or None."""
    code = code.upper()
    raw = _redis_cmd('GET', f'price:{code}')
    if not raw:
        return None
    try:
        entry = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    if 'cached_at' not in entry:
        return None
    age = time.time() - entry['cached_at']
    if age > CACHE_MAX_AGE:
        return None
    if 'prices' not in entry:
        return None

    return {
        'code': code,
        'category': entry.get('category', ''),
        'kuziini_price': entry.get('kuziini_price'),
        'image_url': entry.get('image_url'),
        'prices': entry['prices'],
        'urls': entry.get('urls', {}),
        'cached': True,
        'cache_age_min': round(age / 60),
    }


def set_cached_price(code, data):
    """Store price data for a product code."""
    code = code.upper()
    entry = {
        'prices': data.get('prices', {}),
        'urls': data.get('urls', {}),
        'image_url': data.get('image_url'),
        'category': data.get('category', ''),
        'kuziini_price': data.get('kuziini_price'),
        'cached_at': time.time(),
    }
    payload = json.dumps(entry, ensure_ascii=False)
    # SET with 25h TTL (auto-expire)
    _redis_cmd('SET', f'price:{code}', payload, 'EX', 90000)


def get_cache_status():
    """Get cache metadata (last update, counts)."""
    raw = _redis_cmd('GET', 'cache:status')
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return {'total_cached': 0, 'total_products': 0, 'last_update': None, 'batch_index': 0}


def set_cache_status(status):
    """Update cache metadata."""
    payload = json.dumps(status, ensure_ascii=False)
    _redis_cmd('SET', 'cache:status', payload)


def is_configured():
    """Check if Redis is configured."""
    return bool(REDIS_URL and REDIS_TOKEN)


def test_connection():
    """Test Redis connection. Returns dict with debug info."""
    info = {
        'url_set': bool(REDIS_URL),
        'token_set': bool(REDIS_TOKEN),
        'url_preview': REDIS_URL[:40] + '...' if len(REDIS_URL) > 40 else REDIS_URL,
    }
    if not REDIS_URL or not REDIS_TOKEN:
        info['error'] = 'Missing UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN'
        return info
    try:
        data = json.dumps(['SET', 'test:ping', 'hello']).encode('utf-8')
        req = urllib.request.Request(REDIS_URL, data=data, method='POST')
        req.add_header('Authorization', f'Bearer {REDIS_TOKEN}')
        req.add_header('Content-Type', 'application/json')
        resp = urllib.request.urlopen(req, timeout=5)
        body = resp.read().decode('utf-8')
        info['set_response'] = body
        info['set_status'] = resp.status

        # Now GET it back
        data2 = json.dumps(['GET', 'test:ping']).encode('utf-8')
        req2 = urllib.request.Request(REDIS_URL, data=data2, method='POST')
        req2.add_header('Authorization', f'Bearer {REDIS_TOKEN}')
        req2.add_header('Content-Type', 'application/json')
        resp2 = urllib.request.urlopen(req2, timeout=5)
        body2 = resp2.read().decode('utf-8')
        info['get_response'] = body2
        info['ok'] = True
    except urllib.error.HTTPError as e:
        info['error'] = f'HTTP {e.code}: {e.read().decode("utf-8", errors="replace")[:200]}'
    except Exception as e:
        info['error'] = str(e)
    return info
