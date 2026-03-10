"""
Auth & Offer storage utilities for Kuziini.
Uses only stdlib + _redis_cmd from cache.py (no pip packages).

Redis key schema:
  users              → Hash  {username → JSON user_obj}
  session:{token}    → String user_id, TTL 30 days
  offer:{id}         → String JSON offer obj
  offers:own:{user}  → String JSON list of offer IDs (owned)
  offers:shared:{u}  → String JSON list of offer IDs (shared with)
  offers:all         → String JSON list of ALL offer IDs (admin)
"""
import hashlib
import secrets
import json
import os
import time


# Optional pepper from env for extra hash security
_PEPPER = os.environ.get('KUZIINI_PEPPER', 'kuziini_kz_pepper')


# ── Redis helpers ─────────────────────────────────────────────────────────

def _rc(*args):
    from cache import _redis_cmd
    return _redis_cmd(*args)

def _jget(key):
    raw = _rc('GET', key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None

def _jset(key, val):
    _rc('SET', key, json.dumps(val, ensure_ascii=False))

def _list_prepend(key, item):
    lst = _jget(key) or []
    if item in lst:
        lst.remove(item)
    lst.insert(0, item)
    _jset(key, lst)

def _list_remove(key, item):
    lst = _jget(key) or []
    lst = [i for i in lst if i != item]
    _jset(key, lst)


# ── Password ──────────────────────────────────────────────────────────────

def _hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256(f'{_PEPPER}:{salt}:{password}'.encode('utf-8')).hexdigest()
    return h, salt

def _verify_password(password, stored_hash, salt):
    h, _ = _hash_password(password, salt)
    return h == stored_hash


# ── Users ─────────────────────────────────────────────────────────────────

def get_user(username):
    """Return full user dict or None."""
    raw = _rc('HGET', 'users', username.lower().strip())
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None

def _safe(u):
    """Strip sensitive fields."""
    return {
        'username': u['username'],
        'name': u.get('name', u['username']),
        'role': u['role'],
        'created_at': u.get('created_at'),
    }

def list_users():
    raw = _rc('HGETALL', 'users')
    if not raw:
        return []
    # HGETALL returns [key, val, key, val, ...] from Upstash
    result = []
    if isinstance(raw, list):
        it = iter(raw)
        for k in it:
            v = next(it, None)
            if v:
                try:
                    result.append(_safe(json.loads(v)))
                except Exception:
                    pass
    return result

def create_user(username, password, role='user', name=None):
    uname = username.lower().strip()
    if _rc('HEXISTS', 'users', uname):
        return None, 'Username deja existent'
    pwd_hash, salt = _hash_password(password)
    user = {
        'username': uname,
        'name': name or uname,
        'password_hash': pwd_hash,
        'salt': salt,
        'role': role,
        'created_at': time.time(),
    }
    _rc('HSET', 'users', uname, json.dumps(user, ensure_ascii=False))
    return _safe(user), None

def update_user(username, name=None, role=None, password=None):
    u = get_user(username)
    if not u:
        return None, 'User inexistent'
    if name is not None:
        u['name'] = name
    if role is not None:
        u['role'] = role
    if password:
        u['password_hash'], u['salt'] = _hash_password(password)
    _rc('HSET', 'users', username.lower(), json.dumps(u, ensure_ascii=False))
    return _safe(u), None

def delete_user(username):
    u = get_user(username)
    if not u:
        return 'User inexistent'
    _rc('HDEL', 'users', username.lower())
    return None

def ensure_admin_exists():
    """Auto-create admin/admin123 if no users exist at all."""
    count = _rc('HLEN', 'users')
    if count in (None, 0, '0', b'0'):
        create_user('admin', 'admin123', 'admin', 'Administrator')


# ── Sessions ──────────────────────────────────────────────────────────────

def create_session(username, role):
    token = secrets.token_hex(32)
    payload = json.dumps({'username': username, 'role': role, 'created_at': time.time()})
    _rc('SET', f'session:{token}', payload, 'EX', 2592000)  # 30 days
    return token

def validate_session(token):
    if not token:
        return None
    raw = _rc('GET', f'session:{token}')
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None

def destroy_session(token):
    _rc('DEL', f'session:{token}')

def extract_token(auth_header):
    if auth_header and auth_header.startswith('Bearer '):
        t = auth_header[7:].strip()
        return t if t else None
    return None

def do_login(username, password):
    ensure_admin_exists()
    u = get_user(username)
    if not u:
        return None, None, 'Username sau parola incorecte'
    if not _verify_password(password, u['password_hash'], u['salt']):
        return None, None, 'Username sau parola incorecte'
    token = create_session(u['username'], u['role'])
    return _safe(u), token, None


# ── Offers ────────────────────────────────────────────────────────────────

def save_offer(offer_data, owner_username):
    oid = offer_data.get('num') or offer_data.get('id')
    if not oid:
        return None, 'ID oferta lipsa'

    existing = _jget(f'offer:{oid}')
    shared_with = []
    if existing:
        if existing.get('owner_id') != owner_username:
            return None, 'Acces interzis'
        shared_with = existing.get('shared_with', [])

    offer = dict(offer_data)
    offer['owner_id'] = owner_username
    offer['shared_with'] = shared_with
    # Remove sensitive user display name from stored/printed data — only ID
    offer.pop('owner_name', None)

    _jset(f'offer:{oid}', offer)
    _list_prepend(f'offers:own:{owner_username}', oid)
    _list_prepend('offers:all', oid)
    return oid, None

def get_offer_full(oid, username, role):
    o = _jget(f'offer:{oid}')
    if not o:
        return None, 'Oferta inexistenta'
    if role == 'admin':
        return o, None
    if o.get('owner_id') == username or username in o.get('shared_with', []):
        return o, None
    return None, 'Acces interzis'

def _offer_summary(o):
    prods = o.get('products', [])
    return {
        'id': o.get('num') or o.get('id'),
        'num': o.get('num'),
        'date': o.get('date', ''),
        'client': o.get('client', ''),
        'phone': o.get('phone', ''),
        'total': o.get('total'),
        'discount': o.get('discount', 0),
        'owner_id': o.get('owner_id', ''),
        'shared_with': o.get('shared_with', []),
        'products_count': len(prods),
        'products_qty': sum(p.get('qty', 1) for p in prods),
        'product_codes': [p.get('code', '') for p in prods],
    }

def list_offers(username, role):
    if role == 'admin':
        ids = _jget('offers:all') or []
    else:
        own = _jget(f'offers:own:{username}') or []
        shared = _jget(f'offers:shared:{username}') or []
        seen = set()
        ids = []
        for i in own + shared:
            if i not in seen:
                ids.append(i)
                seen.add(i)

    offers = []
    for oid in ids:
        o = _jget(f'offer:{oid}')
        if o:
            offers.append(_offer_summary(o))
    return offers

def share_offer(oid, requester, requester_role, target_username):
    o = _jget(f'offer:{oid}')
    if not o:
        return 'Oferta inexistenta'
    if requester_role != 'admin' and o.get('owner_id') != requester:
        return 'Acces interzis'
    target = get_user(target_username)
    if not target:
        return f'Userul "{target_username}" nu exista'
    if target['username'] == requester:
        return 'Nu poti partaja cu tine insuti'

    shared = o.get('shared_with', [])
    if target['username'] not in shared:
        shared.append(target['username'])
    o['shared_with'] = shared
    _jset(f'offer:{oid}', o)
    _list_prepend(f'offers:shared:{target["username"]}', oid)
    return None

def delete_offer(oid, username, role):
    o = _jget(f'offer:{oid}')
    if not o:
        return 'Oferta inexistenta'
    if role != 'admin' and o.get('owner_id') != username:
        return 'Acces interzis'
    owner = o.get('owner_id', username)
    _rc('DEL', f'offer:{oid}')
    _list_remove(f'offers:own:{owner}', oid)
    _list_remove('offers:all', oid)
    for u in o.get('shared_with', []):
        _list_remove(f'offers:shared:{u}', oid)
    return None
