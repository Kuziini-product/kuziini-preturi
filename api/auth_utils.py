"""
Auth & Offer storage utilities for Kuziini.
Uses only stdlib + _redis_cmd from cache.py (no pip packages).

Permissions model stored per user:
  {
    "offers":  "own" | "global",
    "reports": ["prices", "history", "stats", "events"],
    "exports": ["excel", "pdf_offer", "pdf_specs"]
  }

Role presets (convenience templates):
  admin   → global offers + all reports + all exports
  manager → global offers + prices/history/stats + excel/pdf_offer
  agent   → own offers + stats + excel/pdf_offer
  viewer  → own offers + stats + no exports
  custom  → manually configured

Redis key schema:
  users              → Hash  {username → JSON user_obj}
  session:{token}    → String JSON session, TTL 30 days
  offer:{id}         → String JSON offer obj
  offers:own:{user}  → String JSON list of offer IDs (owned)
  offers:shared:{u}  → String JSON list of offer IDs (shared)
  offers:all         → String JSON list of ALL offer IDs
"""
import hashlib
import secrets
import json
import os
import time


_PEPPER = os.environ.get('KUZIINI_PEPPER', 'kuziini_kz_pepper')

# Palette of chat colors assigned automatically to new users (cycles if more than 12 users)
CHAT_COLOR_PALETTE = [
    '#7c3aed', '#059669', '#dc2626', '#2563eb',
    '#d97706', '#db2777', '#0891b2', '#65a30d',
    '#7c2d12', '#1d4ed8', '#047857', '#9333ea',
]

# ── Role presets ──────────────────────────────────────────────────────────

ROLE_PRESETS = {
    'admin': {
        'offers':         'global',
        'reports':        ['prices', 'history', 'stats', 'events'],
        'exports':        ['excel', 'pdf_offer', 'pdf_specs'],
        'vendors':        ['samsung', 'emag', 'flanco', 'altex'],
        'show_kz_price':  True,
        'allow_discount': True,
    },
    'manager': {
        'offers':         'global',
        'reports':        ['prices', 'history', 'stats'],
        'exports':        ['excel', 'pdf_offer'],
        'vendors':        ['samsung', 'emag', 'flanco', 'altex'],
        'show_kz_price':  True,
        'allow_discount': True,
    },
    'agent': {
        'offers':         'own',
        'reports':        ['stats'],
        'exports':        ['excel', 'pdf_offer'],
        'vendors':        ['samsung', 'emag', 'flanco', 'altex'],
        'show_kz_price':  False,
        'allow_discount': False,
    },
    'viewer': {
        'offers':         'own',
        'reports':        ['stats'],
        'exports':        [],
        'vendors':        ['samsung', 'emag', 'flanco', 'altex'],
        'show_kz_price':  False,
        'allow_discount': False,
    },
}

ALL_REPORTS  = ['prices', 'history', 'stats', 'events']
ALL_EXPORTS  = ['excel', 'pdf_offer', 'pdf_specs']
ALL_VENDORS  = ['samsung', 'emag', 'flanco', 'altex']


def default_permissions(role):
    return dict(ROLE_PRESETS.get(role, ROLE_PRESETS['viewer']))


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
    raw = _rc('HGET', 'users', username.lower().strip())
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None

def _safe(u):
    """Public-safe user dict (no password fields)."""
    return {
        'username': u['username'],
        'name': u.get('name', u['username']),
        'role': u['role'],
        'user_id': u.get('user_id', ''),
        'chat_color': u.get('chat_color', '#7c3aed'),
        'permissions': u.get('permissions', default_permissions(u['role'])),
        'created_at': u.get('created_at'),
    }

def list_users():
    raw = _rc('HGETALL', 'users')
    if not raw:
        return []
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

def create_user(username, password, role='agent', name=None, permissions=None):
    uname = username.lower().strip()
    if _rc('HEXISTS', 'users', uname):
        return None, 'Username deja existent'
    if role == 'admin':
        perms = dict(ROLE_PRESETS['admin'])
    else:
        perms = permissions if permissions else default_permissions(role)
    pwd_hash, salt = _hash_password(password)
    # Auto-increment user ID: KZ001, KZ002, …
    seq = _rc('INCR', 'user_id_seq')
    try:
        seq = int(seq)
    except (TypeError, ValueError):
        seq = 1
    user_id = f'KZ{seq:03d}'
    chat_color = CHAT_COLOR_PALETTE[(seq - 1) % len(CHAT_COLOR_PALETTE)]
    user = {
        'username': uname,
        'name': name or uname,
        'password_hash': pwd_hash,
        'salt': salt,
        'role': role,
        'user_id': user_id,
        'chat_color': chat_color,
        'permissions': perms,
        'created_at': time.time(),
    }
    _rc('HSET', 'users', uname, json.dumps(user, ensure_ascii=False))
    return _safe(user), None

def update_user(username, name=None, role=None, password=None, permissions=None, chat_color=None):
    u = get_user(username)
    if not u:
        return None, 'User inexistent'
    if name is not None:
        u['name'] = name
    if role is not None:
        u['role'] = role
        # If role changed and no explicit permissions given, reset to preset
        if permissions is None and role in ROLE_PRESETS:
            u['permissions'] = dict(ROLE_PRESETS[role])
    if permissions is not None:
        # admin always keeps full permissions
        if u['role'] == 'admin':
            u['permissions'] = dict(ROLE_PRESETS['admin'])
        else:
            u['permissions'] = permissions
    if chat_color:
        u['chat_color'] = chat_color
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
    count = _rc('HLEN', 'users')
    if count in (None, 0, '0', b'0'):
        create_user('admin', 'admin123', 'admin', 'Administrator')


# ── Sessions ──────────────────────────────────────────────────────────────

def create_session(username, role, permissions, user_id=''):
    token = secrets.token_hex(32)
    payload = json.dumps({
        'username': username,
        'role': role,
        'user_id': user_id,
        'permissions': permissions,
        'created_at': time.time(),
    })
    _rc('SET', f'session:{token}', payload, 'EX', 2592000)
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
    # admin always gets full permissions regardless of stored value
    perms = dict(ROLE_PRESETS['admin']) if u['role'] == 'admin' else u.get('permissions', default_permissions(u['role']))
    token = create_session(u['username'], u['role'], perms, u.get('user_id', ''))
    log_activity(u['username'], 'login')
    return _safe(u), token, None


# ── Activity logging ──────────────────────────────────────────────────────
# Action types:
#   login        – user authenticated
#   cart_add     – product added to cart        {code, category}
#   offer_gen    – offer window opened/created  {offer_id}
#   offer_save   – offer saved                  {offer_id, client}
#   export_excel – Excel export                 {offer_id}
#   export_pdf   – Print/PDF of offer           {offer_id}
#   specs_pdf    – Print/PDF of specs           {code}

_MAX_PER_USER = 500
_MAX_GLOBAL   = 2000

def log_activity(username, action, data=None):
    entry = {
        'ts':       time.time(),
        'username': username,
        'action':   action,
        'data':     data or {},
    }
    raw = json.dumps(entry, ensure_ascii=False)

    # Per-user log
    key_u = f'actlog:{username}'
    lst_u = _jget(key_u) or []
    lst_u.insert(0, entry)
    if len(lst_u) > _MAX_PER_USER:
        lst_u = lst_u[:_MAX_PER_USER]
    _jset(key_u, lst_u)

    # Global log
    key_g = 'actlog:all'
    lst_g = _jget(key_g) or []
    lst_g.insert(0, entry)
    if len(lst_g) > _MAX_GLOBAL:
        lst_g = lst_g[:_MAX_GLOBAL]
    _jset(key_g, lst_g)

def get_activity_report():
    """Return both the global log and per-user summary."""
    log = _jget('actlog:all') or []

    # Build per-user summary
    summary = {}
    for e in log:
        u = e.get('username', '?')
        if u not in summary:
            summary[u] = {
                'username': u,
                'logins': 0, 'cart_adds': 0,
                'offers_gen': 0, 'offers_saved': 0,
                'exports_excel': 0, 'exports_pdf': 0, 'specs_pdf': 0,
                'last_seen': None,
            }
        s = summary[u]
        a = e.get('action', '')
        if a == 'login':        s['logins']        += 1
        elif a == 'cart_add':   s['cart_adds']     += 1
        elif a == 'offer_gen':  s['offers_gen']    += 1
        elif a == 'offer_save': s['offers_saved']  += 1
        elif a == 'export_excel': s['exports_excel'] += 1
        elif a == 'export_pdf': s['exports_pdf']   += 1
        elif a == 'specs_pdf':  s['specs_pdf']     += 1
        ts = e.get('ts')
        if ts and (s['last_seen'] is None or ts > s['last_seen']):
            s['last_seen'] = ts

    return {
        'summary': sorted(summary.values(), key=lambda x: x.get('last_seen') or 0, reverse=True),
        'log': log[:200],  # last 200 entries for detail view
    }

def has_permission(session, category, value=None):
    """
    Check session permission.
    category='offers'  → value='global'|'own' (checks if offers==value, or just has access)
    category='reports' → value='prices'|'history'|'stats'|'events'
    category='exports' → value='excel'|'pdf_offer'|'pdf_specs'
    Admin always passes.
    """
    if session.get('role') == 'admin':
        return True
    perms = session.get('permissions') or {}
    if category == 'offers':
        offers_perm = perms.get('offers', 'own')
        if value is None:
            return True  # has some offers access
        return offers_perm == value or (value == 'own' and offers_perm == 'global')
    elif category == 'reports':
        return value in (perms.get('reports') or [])
    elif category == 'exports':
        return value in (perms.get('exports') or [])
    return False


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
    offer.pop('owner_name', None)  # never store display name

    _jset(f'offer:{oid}', offer)
    _list_prepend(f'offers:own:{owner_username}', oid)
    _list_prepend('offers:all', oid)
    return oid, None

def get_offer_full(oid, username, session):
    o = _jget(f'offer:{oid}')
    if not o:
        return None, 'Oferta inexistenta'
    if has_permission(session, 'offers', 'global'):
        return o, None
    if o.get('owner_id') == username or username in o.get('shared_with', []):
        return o, None
    return None, 'Acces interzis'

def _offer_summary(o):
    prods = o.get('products', [])
    oid = o.get('num') or o.get('id')
    try: chat_count = int(_rc('LLEN', f'offer_chat:{oid}') or 0)
    except: chat_count = 0
    return {
        'id':             oid,
        'num':            o.get('num'),
        'date':           o.get('date', ''),
        'client':         o.get('client', ''),
        'phone':          o.get('phone', ''),
        'email':          o.get('email', ''),
        'total':          o.get('total'),
        'discount':       o.get('discount', 0),
        'owner_id':       o.get('owner_id', ''),
        'shared_with':    o.get('shared_with', []),
        'products_count': len(prods),
        'products_qty':   sum(p.get('qty', 1) for p in prods),
        'product_codes':  [p.get('code', '') for p in prods],
        'chat_count':     chat_count,
    }

def list_offers(username, session):
    if has_permission(session, 'offers', 'global'):
        ids = _jget('offers:all') or []
    else:
        own    = _jget(f'offers:own:{username}') or []
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

def share_offer(oid, requester, session, target_username):
    o = _jget(f'offer:{oid}')
    if not o:
        return 'Oferta inexistenta'
    if not has_permission(session, 'offers', 'global') and o.get('owner_id') != requester:
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

def share_offer_multi(oid, requester, session, target_usernames):
    """Share an offer with multiple users. Returns list of errors (empty = all OK)."""
    o = _jget(f'offer:{oid}')
    if not o:
        return ['Oferta inexistenta']
    if not has_permission(session, 'offers', 'global') and o.get('owner_id') != requester:
        return ['Acces interzis']
    shared = o.get('shared_with', [])
    errors = []
    for uname in target_usernames:
        uname = uname.strip()
        if not uname:
            continue
        if uname == requester:
            errors.append(f'Nu poti partaja cu tine insuti ({uname})')
            continue
        target = get_user(uname)
        if not target:
            errors.append(f'Userul "{uname}" nu exista')
            continue
        if uname not in shared:
            shared.append(uname)
            _list_prepend(f'offers:shared:{uname}', oid)
    o['shared_with'] = shared
    _jset(f'offer:{oid}', o)
    return errors


# ── App settings (stored in Redis) ────────────────────────────────────────

def get_app_settings():
    raw = _rc('GET', 'app:settings')
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}

def save_app_settings(settings):
    _rc('SET', 'app:settings', json.dumps(settings, ensure_ascii=False))


def _user_color_map(usernames):
    """Return {username: chat_color} for a list of usernames."""
    colors = {}
    for uname in usernames:
        u = get_user(uname)
        colors[uname] = u.get('chat_color', '#7c3aed') if u else '#7c3aed'
    return colors

def get_offer_chat(oid, offer=None):
    raw = _rc('LRANGE', f'offer_chat:{oid}', 0, -1) or []
    messages = []
    for m in raw:
        try: messages.append(json.loads(m))
        except: pass
    # Enrich messages with chat_color from user profile
    authors = list({m['username'] for m in messages if m.get('username')})
    color_map = _user_color_map(authors)
    for m in messages:
        m['color'] = color_map.get(m.get('username', ''), '#7c3aed')
    return messages

def get_offer_participants(offer):
    """Return list of {username, name, color} for owner + shared_with."""
    usernames = []
    owner = offer.get('owner_id')
    if owner:
        usernames.append(owner)
    for u in offer.get('shared_with', []):
        if u not in usernames:
            usernames.append(u)
    participants = []
    for uname in usernames:
        u = get_user(uname)
        if u:
            participants.append({
                'username': uname,
                'name': u.get('name', uname),
                'color': u.get('chat_color', '#7c3aed'),
            })
        else:
            participants.append({'username': uname, 'name': uname, 'color': '#7c3aed'})
    return participants

def add_offer_chat(oid, username, name, text):
    import time as _t
    msg = {'username': username, 'name': name, 'text': text, 'ts': int(_t.time())}
    _rc('RPUSH', f'offer_chat:{oid}', json.dumps(msg, ensure_ascii=False))
    _rc('LTRIM', f'offer_chat:{oid}', -50, -1)
    return get_offer_chat(oid)

def delete_offer(oid, username, session):
    o = _jget(f'offer:{oid}')
    if not o:
        return 'Oferta inexistenta'
    if not has_permission(session, 'offers', 'global') and o.get('owner_id') != username:
        return 'Acces interzis'
    owner = o.get('owner_id', username)
    _rc('DEL', f'offer:{oid}')
    _list_remove(f'offers:own:{owner}', oid)
    _list_remove('offers:all', oid)
    for u in o.get('shared_with', []):
        _list_remove(f'offers:shared:{u}', oid)
    return None
