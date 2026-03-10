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

CACHE_MAX_AGE = 48 * 3600  # 48 ore - persista pana la urmatorul cron


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
    # SET with 49h TTL (auto-expire, covers gap between 2 cron runs)
    _redis_cmd('SET', f'price:{code}', payload, 'EX', 176400)


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


def save_cron_event(code, event_type, details=''):
    """Save a cron event (vendor error, unavailability, etc). Key: events:{date}."""
    today = time.strftime('%Y-%m-%d', time.gmtime())
    now = time.strftime('%H:%M', time.gmtime())
    raw = _redis_cmd('GET', f'events:{today}')
    events = []
    if raw:
        try:
            events = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    events.append({
        'time': now,
        'code': code.upper(),
        'type': event_type,
        'details': details,
    })
    # Keep max 500 events per day
    if len(events) > 500:
        events = events[-500:]
    payload = json.dumps(events, ensure_ascii=False)
    _redis_cmd('SET', f'events:{today}', payload, 'EX', 604800)  # 7 days


def get_cron_events(date=None):
    """Get cron events for a date. Returns list of events."""
    if not date:
        date = time.strftime('%Y-%m-%d', time.gmtime())
    raw = _redis_cmd('GET', f'events:{date}')
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def save_price_history(code, prices):
    """Save daily price snapshot for a product. Key: history:{code}, stores last 90 days."""
    code = code.upper()
    today = time.strftime('%Y-%m-%d', time.gmtime())

    # Get existing history
    raw = _redis_cmd('GET', f'history:{code}')
    history = {}
    if raw:
        try:
            history = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass

    # Add today's snapshot (overwrite if already exists for today)
    history[today] = {
        'samsung': prices.get('samsung'),
        'emag': prices.get('emag'),
        'flanco': prices.get('flanco'),
        'altex': prices.get('altex'),
    }

    # Keep only last 90 days
    sorted_dates = sorted(history.keys(), reverse=True)[:90]
    history = {d: history[d] for d in sorted_dates}

    payload = json.dumps(history, ensure_ascii=False)
    # 91 days TTL
    _redis_cmd('SET', f'history:{code}', payload, 'EX', 7862400)


def get_price_history(code):
    """Get price history for a product. Returns dict {date: {vendor: price}}."""
    code = code.upper()
    raw = _redis_cmd('GET', f'history:{code}')
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def get_all_history_codes():
    """Get all product codes that have history data. Uses KEYS command."""
    result = _redis_cmd('KEYS', 'history:*')
    if result and isinstance(result, list):
        return [k.replace('history:', '') for k in result]
    return []


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


# ─── Archive: prices + URLs persistente (fara TTL) ────────────────────────────
# Stocate in Redis ca Hash (HSET/HGET/HGETALL).
# Nu expira — cron-ul le regenereaza zilnic.

def set_product_archive(code, prices, urls, kuziini_price=None, category='', image_url=None):
    """Salveaza prices + URLs in arhiva permanenta (fara TTL). Apelat de cron dupa fiecare produs."""
    code = code.upper()
    entry = {
        'vendors': {
            v: {
                'price': prices.get(v) if prices else None,
                'url': urls.get(v) if urls else None,
            }
            for v in ['samsung', 'emag', 'flanco', 'altex']
        },
        'kuziini_price': round(kuziini_price, 2) if kuziini_price else None,
        'category': category,
        'image_url': image_url,
        'updated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    payload = json.dumps(entry, ensure_ascii=False)
    _redis_cmd('HSET', 'archive:prices', code, payload)


def get_product_archive(code):
    """Returneaza entry-ul de arhiva pentru un singur produs."""
    code = code.upper()
    raw = _redis_cmd('HGET', 'archive:prices', code)
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def get_full_archive():
    """Returneaza arhiva completa pentru toate produsele (HGETALL)."""
    raw = _redis_cmd('HGETALL', 'archive:prices')
    if not raw or not isinstance(raw, list):
        return {}
    # HGETALL returneaza lista plata [key, value, key, value, ...]
    result = {}
    for i in range(0, len(raw) - 1, 2):
        try:
            result[raw[i]] = json.loads(raw[i + 1])
        except (json.JSONDecodeError, TypeError, IndexError):
            pass
    return result
