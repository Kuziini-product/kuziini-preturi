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
from scraper import search_product, search_single_vendor, load_products, APP_VERSION, warmup_session, get_samsung_specs
from cache import get_cached_price, get_cache_status, is_configured as cache_configured, test_connection as cache_test, get_price_history, get_all_history_codes, get_cron_events
import auth_utils

# Warmup on cold start
warmup_session()
_start_time = time.time()


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

            # Verifica cache-ul Redis
            if cache_configured():
                cached = get_cached_price(code)
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
                'message': 'Preturile nu sunt inca disponibile. Se actualizeaza automat.',
                'excel_load_ms': round(_elapsed * 1000),
            })

        elif path == '/api/version':
            self._json({
                'version': _start_time,
                'app_version': APP_VERSION,
                'cache_configured': cache_configured(),
            })

        elif path == '/api/reload_excel':
            import scraper as scraper_mod
            scraper_mod._products_cache = None
            products = load_products()
            self._json({'ok': True, 'count': len(products)})

        elif path == '/api/ping':
            self._json({'pong': True, 'time': time.time()})

        elif path == '/api/test_redis':
            self._json(cache_test())

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

        elif path == '/api/products':
            import re
            products = load_products()
            product_list = []
            for code, info in products.items():
                inches_match = re.search(r'QE(\d{2})', code)
                inches = int(inches_match.group(1)) if inches_match else None
                product_list.append({
                    'code': code,
                    'group': info.get('group', ''),
                    'category': info.get('category', ''),
                    'price': round(info.get('price', 0), 2),
                    'inches': inches,
                })
            product_list.sort(key=lambda x: (x.get('group', ''), x['category'], x.get('inches') or 0, x['code']))
            self._json({'products': product_list, 'count': len(product_list)})

        elif path == '/api/cache_status':
            status = get_cache_status()
            last = status.get('last_update')
            completed = status.get('completed_at')
            now = time.time()
            age = round((now - last) / 60) if last else None
            completed_age = round((now - completed) / 60) if completed else None
            self._json({
                'total_cached': status.get('total_cached', 0),
                'total_products': status.get('total_products', 0),
                'last_update_min_ago': age,
                'last_update_ts': last,
                'completed_at_min_ago': completed_age,
                'completed_at_ts': completed,
                'batch_index': status.get('batch_index', 0),
                'cache_backend': 'redis' if cache_configured() else 'none',
            })

        elif path == '/api/specs':
            code = params.get('code', [''])[0].strip().upper()
            if not code:
                self._json({'error': 'Codul produsului este gol.'}, 400)
                return

            # Verifica cache Redis
            if cache_configured():
                from cache import _redis_cmd
                raw = _redis_cmd('GET', f'specs:{code}')
                if raw:
                    try:
                        cached_specs = json.loads(raw)
                        self._json({'code': code, 'specs': cached_specs, 'cached': True})
                        return
                    except (json.JSONDecodeError, TypeError):
                        pass

            # Scrape live
            specs = get_samsung_specs(code)
            if specs:
                # Cache in Redis (7 zile TTL)
                if cache_configured():
                    from cache import _redis_cmd
                    payload = json.dumps(specs, ensure_ascii=False)
                    _redis_cmd('SET', f'specs:{code}', payload, 'EX', 604800)
                self._json({'code': code, 'specs': specs})
            else:
                self._json({'code': code, 'specs': None, 'message': 'Specificatii indisponibile'})

        elif path == '/api/reports':
            # Rapoarte miscare preturi
            code = params.get('code', [''])[0].strip().upper()

            if code:
                # Istoric pret pentru un singur produs
                history = get_price_history(code)
                # Adauga pretul Kuziini din Excel
                products = load_products()
                kuziini_price = None
                if code in products:
                    kuziini_price = round(products[code]['price'], 2)
                self._json({
                    'code': code,
                    'kuziini_price': kuziini_price,
                    'history': history,
                    'days': len(history),
                })
            else:
                # Sumar: toate produsele cu istoric
                codes = get_all_history_codes()
                products = load_products()
                summary = []
                for c in codes:
                    hist = get_price_history(c)
                    if not hist:
                        continue
                    dates = sorted(hist.keys())
                    latest_date = dates[-1]
                    latest = hist[latest_date]
                    # Calculeaza schimbare fata de prima zi disponibila
                    first_date = dates[0]
                    first = hist[first_date]
                    changes = {}
                    for v in ['samsung', 'emag', 'flanco', 'altex']:
                        cur = latest.get(v)
                        prev = first.get(v)
                        if cur is not None and prev is not None and prev > 0:
                            changes[v] = round(cur - prev, 2)
                    kz = None
                    cat = ''
                    if c in products:
                        kz = round(products[c]['price'], 2)
                        cat = products[c].get('category', '')
                    summary.append({
                        'code': c,
                        'category': cat,
                        'kuziini_price': kz,
                        'latest': latest,
                        'latest_date': latest_date,
                        'first_date': first_date,
                        'changes': changes,
                        'days_tracked': len(dates),
                    })
                summary.sort(key=lambda x: x['code'])
                self._json({
                    'products': summary,
                    'count': len(summary),
                })

        elif path == '/api/events':
            # Evenimente cron (erori vendori, timeout-uri)
            date = params.get('date', [''])[0].strip()
            events = get_cron_events(date if date else None)
            self._json({
                'date': date if date else time.strftime('%Y-%m-%d', time.gmtime()),
                'events': events,
                'count': len(events),
            })

        elif path == '/api/archive':
            # Returneaza arhiva completa (prices + URLs pentru toate produsele)
            if cache_configured():
                from cache import get_full_archive
                archive = get_full_archive()
                self._json(archive)
            else:
                self._json({})

        else:
            self._json({'error': 'Not found'}, 404)

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            return {}

    def _require_auth(self, require_admin=False):
        token = auth_utils.extract_token(self.headers.get('Authorization', ''))
        session = auth_utils.validate_session(token)
        if not session:
            self._json({'error': 'Autentificare necesara.'}, 401)
            return None
        if require_admin and session.get('role') != 'admin':
            self._json({'error': 'Acces interzis. Necesita rol admin.'}, 403)
            return None
        return session

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_body()

        # ── Auth ──────────────────────────────────────────────────────────

        if path == '/api/auth/login':
            username = (body.get('username') or '').strip()
            password = body.get('password') or ''
            if not username or not password:
                self._json({'error': 'Username si parola sunt obligatorii.'}, 400)
                return
            user, token, err = auth_utils.do_login(username, password)
            if err:
                self._json({'error': err}, 401)
                return
            self._json({'token': token, 'username': user['username'],
                        'name': user['name'], 'role': user['role']})

        elif path == '/api/auth/logout':
            token = auth_utils.extract_token(self.headers.get('Authorization', ''))
            if token:
                auth_utils.destroy_session(token)
            self._json({'ok': True})

        elif path == '/api/auth/me':
            session = self._require_auth()
            if not session:
                return
            self._json({'username': session['username'], 'role': session['role']})

        # ── Offers ────────────────────────────────────────────────────────

        elif path == '/api/offers/save':
            session = self._require_auth()
            if not session:
                return
            oid, err = auth_utils.save_offer(body, session['username'])
            if err:
                self._json({'error': err}, 403)
                return
            self._json({'ok': True, 'offer_id': oid})

        elif path == '/api/offers/list':
            session = self._require_auth()
            if not session:
                return
            offers = auth_utils.list_offers(session['username'], session['role'])
            self._json({'offers': offers})

        elif path == '/api/offers/get':
            session = self._require_auth()
            if not session:
                return
            oid = body.get('offer_id') or ''
            offer, err = auth_utils.get_offer_full(oid, session['username'], session['role'])
            if err:
                self._json({'error': err}, 403)
                return
            self._json({'offer': offer})

        elif path == '/api/offers/share':
            session = self._require_auth()
            if not session:
                return
            oid = body.get('offer_id') or ''
            target = (body.get('target_username') or '').strip()
            if not oid or not target:
                self._json({'error': 'offer_id si target_username sunt obligatorii.'}, 400)
                return
            err = auth_utils.share_offer(oid, session['username'], session['role'], target)
            if err:
                self._json({'error': err}, 400)
                return
            self._json({'ok': True})

        elif path == '/api/offers/delete':
            session = self._require_auth()
            if not session:
                return
            oid = body.get('offer_id') or ''
            if not oid:
                self._json({'error': 'offer_id obligatoriu.'}, 400)
                return
            err = auth_utils.delete_offer(oid, session['username'], session['role'])
            if err:
                self._json({'error': err}, 403)
                return
            self._json({'ok': True})

        # ── Users (admin) ─────────────────────────────────────────────────

        elif path == '/api/users/list':
            session = self._require_auth(require_admin=True)
            if not session:
                return
            self._json({'users': auth_utils.list_users()})

        elif path == '/api/users/create':
            session = self._require_auth(require_admin=True)
            if not session:
                return
            username = (body.get('username') or '').strip()
            password = body.get('password') or ''
            role     = body.get('role') or 'user'
            name     = (body.get('name') or '').strip() or username
            if not username or not password:
                self._json({'error': 'Username si parola sunt obligatorii.'}, 400)
                return
            if role not in ('admin', 'user'):
                role = 'user'
            user, err = auth_utils.create_user(username, password, role, name)
            if err:
                self._json({'error': err}, 400)
                return
            self._json({'ok': True, 'user': user})

        elif path == '/api/users/update':
            session = self._require_auth(require_admin=True)
            if not session:
                return
            username = (body.get('username') or '').strip()
            if not username:
                self._json({'error': 'username obligatoriu.'}, 400)
                return
            user, err = auth_utils.update_user(
                username,
                name=body.get('name'),
                role=body.get('role'),
                password=body.get('password'),
            )
            if err:
                self._json({'error': err}, 400)
                return
            self._json({'ok': True, 'user': user})

        elif path == '/api/users/delete':
            session = self._require_auth(require_admin=True)
            if not session:
                return
            username = (body.get('username') or '').strip()
            if username == session['username']:
                self._json({'error': 'Nu te poti sterge pe tine insuti.'}, 400)
                return
            err = auth_utils.delete_user(username)
            if err:
                self._json({'error': err}, 400)
                return
            self._json({'ok': True})

        elif path == '/api/users/change_password':
            session = self._require_auth()
            if not session:
                return
            # Admin can reset any user; regular user must confirm old password
            target_user = (body.get('username') or session['username']).strip()
            new_pwd = body.get('new_password') or ''
            if not new_pwd:
                self._json({'error': 'Parola noua obligatorie.'}, 400)
                return
            if session['role'] != 'admin' or target_user == session['username']:
                # Must verify old password
                old_pwd = body.get('old_password') or ''
                u = auth_utils.get_user(session['username'])
                if not u or not auth_utils._verify_password(old_pwd, u['password_hash'], u['salt']):
                    self._json({'error': 'Parola curenta incorecta.'}, 401)
                    return
                target_user = session['username']
            _, err = auth_utils.update_user(target_user, password=new_pwd)
            if err:
                self._json({'error': err}, 400)
                return
            self._json({'ok': True})

        else:
            self._json({'error': 'Not found'}, 404)
