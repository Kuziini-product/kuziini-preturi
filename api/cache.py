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
