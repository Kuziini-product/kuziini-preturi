#!/usr/bin/env python3
"""
Kuziini X Samsung - Scraper Module (Vercel Serverless)
"""
APP_VERSION = "v10-vercel"

import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse

# openpyxl importat lazy in load_products() - heavy pe cold start
import requests
from bs4 import BeautifulSoup

# ─── Configurare ────────────────────────────────────────────────────────────

# Pe Vercel, BASE_DIR = /var/task/api, data = /var/task/data
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)  # un nivel mai sus (root proiect)
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')

IS_VERCEL = os.environ.get('VERCEL', '') == '1' or os.environ.get('VERCEL_ENV', '') != ''
IS_WINDOWS = platform.system() == 'Windows'
FINEDATA_API_KEY = os.environ.get('FINEDATA_API_KEY', '')

# Pe Vercel timeout-uri mai scurte (10s total per functie pe Hobby)
# Cron mode seteaza CURL_TIMEOUT la 15s (cron are 60s)
CURL_TIMEOUT = 15 if IS_VERCEL else 12

def set_cron_timeouts():
    """Mareste timeout-urile pentru cron mode (60s budget)."""
    global CURL_TIMEOUT
    CURL_TIMEOUT = 15
REQ_TIMEOUT = 5 if IS_VERCEL else 10

EXCEL_FILE = None
# Cauta in data/ (Vercel) si in BASE_DIR (local)
for search_dir in [DATA_DIR, BASE_DIR, PROJECT_ROOT]:
    if not os.path.isdir(search_dir):
        continue
    for fname in os.listdir(search_dir):
        if fname.lower().endswith('.xlsx') and not fname.startswith('~$'):
            EXCEL_FILE = os.path.join(search_dir, fname)
            break
    if EXCEL_FILE:
        break

# ─── Logging (stdout pe Vercel, fisier local) ────────────────────────────────

_logger = None

def get_logger():
    global _logger
    if _logger is None:
        _logger = logging.getLogger('kuziini')
        _logger.setLevel(logging.DEBUG)
        if not IS_VERCEL:
            try:
                fh = logging.FileHandler(os.path.join(BASE_DIR, 'kuziini_debug.log'),
                                         encoding='utf-8', mode='a')
                fh.setLevel(logging.DEBUG)
                fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s',
                                                  datefmt='%H:%M:%S'))
                _logger.addHandler(fh)
            except Exception:
                pass
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.DEBUG if IS_VERCEL else logging.INFO)
        ch.setFormatter(logging.Formatter('%(asctime)s %(message)s' if IS_VERCEL else '%(message)s',
                                          datefmt='%H:%M:%S'))
        _logger.addHandler(ch)
    return _logger

def log(msg, level='info'):
    try:
        getattr(get_logger(), level)(msg)
    except Exception:
        pass

# ─── Headers realiste browser Chrome ─────────────────────────────────────────
# Fara aceste headere, site-urile detecteaza Python ca bot!

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,'
              'image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Sec-Ch-Ua': '"Chromium";v="134", "Not:A-Brand";v="8", "Google Chrome";v="134"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'DNT': '1',
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Initializeaza sesiunea vizitand Google (pentru cookies realiste)
def warmup_session():
    if IS_VERCEL:
        return  # Skip warmup pe Vercel (economisim timp)
    try:
        SESSION.get('https://www.google.com', timeout=5)
    except Exception:
        pass

# ─── Excel ───────────────────────────────────────────────────────────────────

_products_cache = None

def load_products():
    global _products_cache
    if _products_cache is not None:
        return _products_cache

    # Prioritate: JSON pre-generat (rapid, fara openpyxl)
    json_file = os.path.join(DATA_DIR, 'products.json')
    if os.path.isfile(json_file):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                _products_cache = json.load(f)
            print(f"  JSON incarcat: {len(_products_cache)} produse")
            return _products_cache
        except Exception as e:
            print(f"  Eroare JSON: {e}")

    # Fallback: Excel (doar local, nu pe Vercel)
    if not EXCEL_FILE:
        print("  ATENTIE: Fisierul nu a fost gasit!")
        _products_cache = {}
        return _products_cache
    try:
        import openpyxl
        wb = openpyxl.load_workbook(EXCEL_FILE, data_only=True)
        ws = wb.active
        products = {}
        for row in ws.iter_rows(min_row=3, values_only=True):
            model_code = row[1]
            price_col_f = row[5]
            if model_code and price_col_f:
                code = str(model_code).strip().upper()
                try:
                    products[code] = {
                        'category': row[0] or '',
                        'code': code,
                        'price': float(price_col_f)
                    }
                except (ValueError, TypeError):
                    pass
        _products_cache = products
        print(f"  Excel incarcat: {len(products)} produse")
        return products
    except Exception as e:
        print(f"  Eroare Excel: {e}")
        _products_cache = {}
        return _products_cache


# ─── Variante Cod ────────────────────────────────────────────────────────────

def get_search_variants(code):
    """
    Genereaza variante ale codului pentru cautare pe diferite site-uri.
    Ex: QE55QN90FATXXH -> [QE55QN90FATXXH, QE55QN90F, QE55QN90]
    Ex: HW-B400F/EN -> [HW-B400F/EN, HW-B400F, HW-B400]
    """
    variants = [code]
    code_up = code.upper()

    # Elimina sufixul /EN, /ZA, /XD etc. (soundbars, audio)
    if '/' in code_up:
        base_no_slash = code_up.split('/')[0]
        if base_no_slash not in variants:
            variants.append(base_no_slash)
        code_up = base_no_slash  # continua cu baza fara sufix

    # Elimina sufixul de regiune (TXXH, AUXXH, etc.)
    base = code_up
    for pat in [r'(FKXXH)$',
                r'(TXXH|BTXXH|ATXXH|CTXXH|DTXXH|ETXXH|FTXXH)$',
                r'(AUXXH|BUXXH|CUXXH)$',
                r'(AXXXH|BXXXH|CXXXH)$',
                r'(EXXN|BXXN|CXXN)$',
                r'(XXH|XXN|XXU)$']:
        m = re.search(pat, base)
        if m:
            base = base[:m.start()]
            break

    if base != code_up and base not in variants:
        variants.append(base)

    # Elimina si litera de varianta (A, B, C la final)
    if len(base) > 4 and base[-1] in 'ABCDEFGH':
        base2 = base[:-1]
        if base2 not in variants:
            variants.append(base2)

    return variants


def product_matches_code(soup, code, strict=False):
    """
    Verifica daca pagina produsului contine codul cautat (sau o varianta scurta).
    Previne returnarea pretului de la un produs gresit.
    strict=True: nu foloseste varianta cea mai scurta (fara litera de model).
                 Ex: QE55QN90FATXXH -> accepta QE55QN90FATXXH si QE55QN90F,
                 dar NU QE55QN90 (care ar potrivi si QN90B, QN90C, etc.)
    """
    if not soup:
        return False
    page_text = soup.get_text().upper()
    variants = get_search_variants(code)
    if strict and len(variants) > 2:
        # Exclude cea mai scurta varianta (fara litera de model)
        variants = variants[:-1]
    for v in variants:
        if v.upper() in page_text:
            return True
    return False


# ─── Parsare Pret ────────────────────────────────────────────────────────────

def parse_ro_price(text):
    """
    Parseaza format pret romanesc.
    '4.799,99 lei' -> 4799.99
    '799,00 lei'   -> 799.0      (fara separator mii)
    '4.399,00 lei' -> 4399.0
    '21.999 lei'   -> 21999.0   (fara zecimale)
    """
    if not text:
        return None
    text = str(text).strip()

    # Format cu virgula: 4.799,99 sau 799,00
    m = re.search(r'(\d[\d.]*),(\d{2})\b', text)
    if m:
        integer_part = m.group(1).replace('.', '').replace(' ', '')
        decimal_part = m.group(2)
        try:
            return float(integer_part + '.' + decimal_part)
        except ValueError:
            return None

    # Format fara virgula cu separator mii: 4.799 sau 21.999
    m = re.search(r'(\d{1,3}(?:\.\d{3})+)\b', text)
    if m:
        try:
            return float(m.group(1).replace('.', ''))
        except ValueError:
            return None

    # Numar simplu
    m = re.search(r'(\d{3,6})\b', text)
    if m:
        try:
            val = float(m.group(1))
            if val > 100:
                return val
        except ValueError:
            pass

    return None


def find_prices_in_soup(soup, min_price=400, max_price=300000):
    """
    Fallback robust: cauta toate preturile in textul paginii.
    Gestioneaza:
    - 4.799,99 lei (cu separator mii si zecimale)
    - 799,00 lei   (sub 1000 lei, fara separator mii)  <-- Altex
    - 4.399,00 lei (cu separator mii, zecimale .00)
    - 21.999 lei   (fara zecimale)
    """
    prices = set()

    # get_text fara separator combina <strong>4.799</strong><sup>,99</sup> -> '4.799,99'
    full = soup.get_text(separator='')

    # Pattern 1: cu separator mii si virgula decimala (4.799,99)
    for m in re.finditer(r'(\d{1,3}(?:\.\d{3})+,\d{2})', full):
        p = parse_ro_price(m.group(1))
        if p and min_price <= p <= max_price:
            prices.add(p)

    # Pattern 2: fara separator mii, cu virgula si context 'lei' (799,00 lei)
    for m in re.finditer(r'(\d{2,4},\d{2})\s*[Ll]ei', full):
        p = parse_ro_price(m.group(1))
        if p and min_price <= p <= max_price:
            prices.add(p)

    # Pattern 3: cu separator mii fara zecimale si context 'lei' (21.999 lei)
    for m in re.finditer(r'(\d{1,3}(?:\.\d{3})+)\s*[Ll]ei', full):
        p = parse_ro_price(m.group(1))
        if p and min_price <= p <= max_price:
            prices.add(p)

    return sorted(prices)


def extract_json_ld_price(soup):
    """
    Extrage pretul de vanzare real din JSON-LD.
    Colecteaza toate valorile de pret si returneaza minimul (pretul de oferta, nu PRP).
    """
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            text = script.string
            if not text:
                continue
            data = json.loads(text)
            # Suport array JSON-LD: eMAG pune [BreadcrumbList, Product, ...]
            # Iterăm prin TOATE elementele, nu doar data[0]!
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                # @type poate fi string sau array: "Product" sau ["Product", "Thing"]
                dtype = item.get('@type', '')
                if 'Product' not in str(dtype):
                    continue
                offers = item.get('offers', {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if not isinstance(offers, dict):
                    continue
                # Colecteaza TOATE preturile si returneaza minimul
                # (pretul de vanzare e intotdeauna <= PRP)
                candidates = []
                for price_key in ['price', 'lowPrice', 'salePrice', 'offerPrice']:
                    val = offers.get(price_key)
                    if val is not None:
                        try:
                            p = float(str(val).replace(',', '.'))
                            if p > 100:
                                candidates.append(p)
                        except (ValueError, TypeError):
                            pass
                if candidates:
                    return min(candidates)  # pretul de vanzare = cel mai mic pret gasit
        except Exception:
            continue
    return None


def find_price_in_json(data, depth=0):
    """
    Cauta recursiv pretul de VANZARE in JSON (ex: __NEXT_DATA__, Samsung API, eMAG).
    Prioritizeaza preturile de oferta (currentPrice, salePrice) fata de PRP (regularPrice, price).
    """
    if depth > 10 or data is None:
        return None
    if isinstance(data, dict):
        # Prețuri de vânzare — prioritate maximă (nu PRP!)
        SALE_KEYS = [
            'currentPrice', 'salePrice', 'finalPrice', 'offerPrice', 'special_price',
            'sellingPrice', 'priceAmount',
            # Samsung specific (pret de vanzare):
            'lowestSellingPrice', 'salesPrice', 'finalSalesPrice', 'promotionPrice',
            # eMAG specific:
            'price_val', 'price_ron',
            # Altex specific (pretul de vanzare, NU cel vechi):
            'specialPrice', 'special_price', 'selling_price', 'sellingprice',
            'sale_price', 'discounted_price', 'discountedPrice',
            'promoPrice', 'promo_price', 'activePrice', 'active_price',
        ]
        # Prețuri de referință — prioritate mai mică (pot fi PRP/listprice)
        REGULAR_KEYS = [
            'price', 'Price',
            'regularPrice', 'retailPrice', 'formattedPrice', 'displayPrice',
            'sellingPriceDisplay',
            # Altex specific (pretul vechi/intreg - EVITA daca exista sale price):
            'regular_price', 'list_price', 'oldPrice', 'old_price',
            'originalPrice', 'original_price', 'basePrice', 'base_price',
        ]

        def _try_parse(val):
            try:
                ro = parse_ro_price(str(val))
                if ro and 100 < ro < 300000:
                    return ro
                val_str = str(val).replace(',', '.').replace(' ', '')
                val_float = float(re.sub(r'[^\d.]', '', val_str))
                if 100 < val_float < 300000:
                    return val_float
            except Exception:
                pass
            return None

        # Incearca mai intai preturile de vanzare
        for key in SALE_KEYS:
            if key in data:
                p = _try_parse(data[key])
                if p:
                    return p
        # Apoi preturile de referinta (ca fallback)
        for key in REGULAR_KEYS:
            if key in data:
                p = _try_parse(data[key])
                if p:
                    return p

        for v in list(data.values())[:25]:
            r = find_price_in_json(v, depth + 1)
            if r:
                return r
    elif isinstance(data, list):
        for item in data[:8]:
            r = find_price_in_json(item, depth + 1)
            if r:
                return r
    return None


# ─── Agregator Preturi ────────────────────────────────────────────────────────
# Flanco si Altex blocheaza Python la nivel TLS/IP.
# Solutia: compari.ro / priceguru.ro agrega preturile tuturor retailer-ilor.

def extract_vendor_prices_from_page(soup):
    """
    Extrage preturile per retailer dintr-o pagina de comparare preturi.
    Cauta asocieri intre numele retailer-ilor si valorile de pret.
    """
    prices = {}
    vendor_patterns = {
        'emag':    ['emag.ro', 'emag'],
        'flanco':  ['flanco.ro', 'flanco'],
        'altex':   ['altex.ro', 'altex'],
        'samsung': ['samsung.com/ro', 'samsung.ro', 'samsung shop'],
    }

    # Metoda 1: cauta link-uri catre retailer langa preturi
    for a in soup.find_all('a', href=True):
        href = a['href'].lower()
        matched_vendor = None
        for vendor, patterns in vendor_patterns.items():
            if vendor in prices:
                continue
            if any(p in href for p in patterns):
                matched_vendor = vendor
                break
        if not matched_vendor:
            continue
        # Cauta pretul in containerul linkului (max 5 niveluri)
        container = a.parent
        for _ in range(5):
            if container is None:
                break
            t = container.get_text(separator='')
            p = parse_ro_price(t)
            if p and p > 400:
                prices[matched_vendor] = p
                log(f"  Aggregator {matched_vendor} (href): {p}")
                break
            container = container.parent

    # Metoda 2: cauta textul retailer-ului langa pret
    for vendor, patterns in vendor_patterns.items():
        if vendor in prices:
            continue
        for pattern in patterns:
            for elem in soup.find_all(string=re.compile(pattern, re.IGNORECASE)):
                container = elem.parent
                for _ in range(5):
                    if container is None:
                        break
                    t = container.get_text(separator='')
                    p = parse_ro_price(t)
                    if p and p > 400:
                        prices[vendor] = p
                        log(f"  Aggregator {vendor} (text): {p}")
                        break
                    container = container.parent
                if vendor in prices:
                    break
            if vendor in prices:
                break

    return prices


def scrape_price_aggregator(code):
    """
    Fallback pentru Flanco/Altex care sunt blocate.
    Cauta pe compari.ro si priceguru.ro - site-uri care agrega preturile.
    Returneaza dict {vendor: pret} cu ce gaseste.
    """
    log(f"\n--- Aggregator ({code}) ---")

    for variant in get_search_variants(code)[:2]:
        v_enc = urllib.parse.quote(variant)

        # ── compari.ro (curl pentru bypass TLS) ─────────────────────────────
        for search_url in [
            f'https://www.compari.ro/search/?keywords={v_enc}',
            f'https://www.compari.ro/search/?q={v_enc}',
            f'https://www.compari.ro/cauta/?q={v_enc}',
        ]:
            _, soup = get_page_curl(search_url, timeout=10, referer='https://www.compari.ro/')
            if not soup:
                continue
            log(f"  Compari search OK: {search_url[-55:]}")

            # Incearca preturile direct din pagina de cautare
            direct = extract_vendor_prices_from_page(soup)
            if direct:
                log(f"  Compari (search direct): {direct}")
                return direct

            # Cauta link produs care contine codul
            product_url = None
            for a in soup.find_all('a', href=True):
                href = a['href']
                if variant.lower() in href.lower() or code.lower() in href.lower():
                    product_url = href if href.startswith('http') else 'https://www.compari.ro' + href
                    log(f"  Compari product URL (cod): {product_url[:80]}")
                    break
            if not product_url:
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if any(p in href.lower() for p in ['/produs/', '/p/', '/product/', '/pret/']):
                        product_url = href if href.startswith('http') else 'https://www.compari.ro' + href
                        log(f"  Compari product URL (primul): {product_url[:80]}")
                        break
            if product_url:
                _, prod_soup = get_page_curl(product_url, timeout=10, referer=search_url)
                if prod_soup:
                    prices = extract_vendor_prices_from_page(prod_soup)
                    if prices:
                        log(f"  Compari product page: {prices}")
                        return prices
                    log("  Compari product page: niciun pret gasit")

    log("  Aggregator: niciun pret gasit", 'warning')
    return {}


# ─── curl Fetch ───────────────────────────────────────────────────────────────
# Pe Windows: curl.exe cu Schannel (bypass TLS fingerprint Python/OpenSSL)
# Pe Linux/Vercel: curl cu OpenSSL (disponibil in runtime)

_curl_bin = None

def _get_curl_bin():
    global _curl_bin
    if _curl_bin is None:
        candidates = ['curl.exe', 'curl'] if IS_WINDOWS else ['curl']
        for c in candidates:
            if shutil.which(c):
                _curl_bin = c
                break
        if not _curl_bin:
            _curl_bin = 'curl'
    return _curl_bin

# Detecteaza daca curl suporta HTTP/2 (Linux/OpenSSL da, Windows/Schannel nu)
_curl_has_http2 = None
def _curl_supports_http2():
    global _curl_has_http2
    if _curl_has_http2 is None:
        try:
            r = subprocess.run([_get_curl_bin(), '--version'], capture_output=True, timeout=5)
            _curl_has_http2 = b'HTTP2' in r.stdout or b'http2' in r.stdout or b'nghttp2' in r.stdout
        except Exception:
            _curl_has_http2 = False
        log(f"  curl HTTP/2 support: {_curl_has_http2}")
    return _curl_has_http2


def get_page_curl(url, timeout=None, referer=None):
    if timeout is None:
        timeout = CURL_TIMEOUT
    """
    Fetch URL via curl (bypass TLS fingerprint Python/OpenSSL).
    Returneaza (text, soup) sau (None, None).
    """
    cmd = [
        _get_curl_bin(), '-s', '-L',
        '-m', str(timeout),
        '--compressed',
        '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
        '-H', 'Accept: text/html,application/xhtml+xml,application/xml;'
              'q=0.9,image/avif,image/webp,*/*;q=0.8',
        '-H', 'Accept-Language: ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7',
        '-H', 'Sec-Fetch-Dest: document',
        '-H', 'Sec-Fetch-Mode: navigate',
        '-H', 'Sec-Fetch-Site: none',
        '-H', 'Sec-Fetch-User: ?1',
        '-H', 'DNT: 1',
    ]
    if _curl_supports_http2():
        cmd.insert(3, '--http2')
    if referer:
        cmd += ['-H', f'Referer: {referer}', '-H', 'Sec-Fetch-Site: same-origin']
    cmd.append(url)

    try:
        run_kwargs = {'capture_output': True, 'timeout': timeout + 5}
        if IS_WINDOWS:
            run_kwargs['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
        result = subprocess.run(cmd, **run_kwargs)
        if result.returncode == 0 and result.stdout:
            text = result.stdout.decode('utf-8', errors='replace')
            size = len(text)
            log(f"  curl {url[:65]} -> {size:,}b")
            if size > 500:
                return text, BeautifulSoup(text, 'html.parser')
            log(f"  curl: raspuns prea mic ({size}b)", 'warning')
        else:
            err = result.stderr.decode('utf-8', errors='replace')[:150]
            log(f"  curl EROARE (exit {result.returncode}): {err}", 'warning')
    except subprocess.TimeoutExpired:
        log(f"  curl TIMEOUT ({timeout}s): {url[:60]}", 'warning')
    except FileNotFoundError:
        log("  curl negasit pe sistem - folosim requests fallback", 'warning')
        return get_page(url, timeout=timeout, referer=referer)
    except Exception as e:
        log(f"  curl exceptie: {e}", 'error')
    return None, None


def get_page(url, timeout=None, referer=None):
    if timeout is None:
        timeout = REQ_TIMEOUT
    """Fetch URL si returneaza (resp, soup) sau (None, None) la eroare."""
    try:
        headers_extra = {}
        if referer:
            headers_extra['Referer'] = referer
            headers_extra['Sec-Fetch-Site'] = 'same-origin'
        resp = SESSION.get(url, timeout=timeout, allow_redirects=True,
                           headers=headers_extra if headers_extra else None)
        size = len(resp.text)
        log(f"  GET {url[:70]} -> {resp.status_code} ({size:,} bytes)")
        if resp.status_code == 200 and size > 500:
            soup = BeautifulSoup(resp.text, 'html.parser')
            return resp, soup
        elif resp.status_code != 200:
            log(f"  EROARE HTTP {resp.status_code} pentru {url[:60]}", 'warning')
        else:
            log(f"  Pagina prea mica ({size} bytes) - posibil blocat/redirect", 'warning')
            log(f"  Preview: {resp.text[:200]}", 'debug')
    except requests.exceptions.Timeout:
        log(f"  TIMEOUT ({timeout}s) pentru {url[:60]}", 'warning')
    except Exception as e:
        log(f"  EROARE fetch {url[:60]}: {e}", 'error')
    return None, None


# ─── FineData.ai Fetch (anti-bot bypass) ─────────────────────────────────────

def _finedata_fetch(url, js_render=False, timeout=15):
    """
    Fetch URL via FineData.ai API (bypass Cloudflare, Akamai, DataDome).
    Returneaza (text, soup) sau (None, None).
    Necesita FINEDATA_API_KEY in env.
    """
    if not FINEDATA_API_KEY:
        return None, None
    try:
        payload = {
            'url': url,
            'formats': ['rawHtml'],
            'stealth_antibot': True,
            'use_residential': True,
            'tls_profile': 'chrome136',
            'timeout': timeout,
            'max_retries': 2,
        }
        if js_render:
            payload['use_js_render'] = True
            payload['js_wait_for'] = 'networkidle'
        resp = requests.post(
            'https://api.finedata.ai/api/v1/scrape',
            headers={'x-api-key': FINEDATA_API_KEY, 'Content-Type': 'application/json'},
            json=payload,
            timeout=timeout + 10,
        )
        data = resp.json()
        if data.get('success') and data.get('status_code') == 200:
            html = data.get('body', '') or data.get('data', {}).get('rawHtml', '')
            tokens = data.get('tokens_used', '?')
            log(f"  FineData OK {url[:60]} -> {len(html):,}b ({tokens} tokens)")
            if html and len(html) > 500:
                return html, BeautifulSoup(html, 'html.parser')
            log(f"  FineData: raspuns prea mic ({len(html)}b)", 'warning')
        else:
            reason = data.get('meta', {}).get('block_reason', data.get('error', 'unknown'))
            log(f"  FineData EROARE: status={data.get('status_code')}, reason={reason}", 'warning')
    except requests.exceptions.Timeout:
        log(f"  FineData TIMEOUT ({timeout}s): {url[:60]}", 'warning')
    except Exception as e:
        log(f"  FineData exceptie: {e}", 'error')
    return None, None


def _finedata_extract_price(url, product_code, js_render=False, timeout=20):
    """
    FineData cu AI extraction: un singur apel care returneaza pretul si URL-ul produsului.
    Foloseste extract_prompt pentru a cere AI-ului sa extraga pretul.
    Returneaza (price, product_url) sau (None, None).
    """
    if not FINEDATA_API_KEY:
        return None, None
    try:
        payload = {
            'url': url,
            'formats': ['markdown'],
            'stealth_antibot': True,
            'use_residential': True,
            'tls_profile': 'chrome136',
            'timeout': timeout,
            'max_retries': 2,
            'only_main_content': True,
            'extract_prompt': (
                f'Find the product with Samsung code "{product_code}" on this page. '
                f'Return ONLY a JSON object with: '
                f'{{"price": <number or null>, "url": "<product URL or null>", "name": "<product name or null>"}}. '
                f'The price should be a number without currency (e.g. 4899.99). '
                f'If the product is not found, return {{"price": null, "url": null, "name": null}}.'
            ),
        }
        if js_render:
            payload['use_js_render'] = True
            payload['js_wait_for'] = 'networkidle'
        resp = requests.post(
            'https://api.finedata.ai/api/v1/scrape',
            headers={'x-api-key': FINEDATA_API_KEY, 'Content-Type': 'application/json'},
            json=payload,
            timeout=timeout + 10,
        )
        data = resp.json()
        tokens = data.get('tokens_used', '?')
        log(f"  FineData extract {url[:55]} -> tokens={tokens}")
        if data.get('success'):
            extract = data.get('data', {}).get('extract', {})
            # AI extraction result
            ai_data = extract.get('ai_extract', extract) if isinstance(extract, dict) else {}
            if not isinstance(ai_data, dict):
                # Poate fi un string JSON
                try:
                    ai_data = json.loads(str(ai_data))
                except Exception:
                    ai_data = {}
            price = ai_data.get('price')
            prod_url = ai_data.get('url')
            if price and isinstance(price, (int, float)) and price > 100:
                log(f"  FineData AI extract: price={price}, url={prod_url}")
                return (float(price), prod_url or url)
            # Fallback: parsam markdown-ul returnat
            md = data.get('data', {}).get('markdown', '')
            if md:
                prices = re.findall(r'([\d.]+[,.]?\d{0,2})\s*(?:lei|RON|Lei)', md)
                for ps in prices:
                    p = parse_ro_price(ps + ' lei')
                    if p and p > 400:
                        log(f"  FineData markdown price: {p}")
                        return (p, prod_url or url)
    except requests.exceptions.Timeout:
        log(f"  FineData extract TIMEOUT: {url[:55]}", 'warning')
    except Exception as e:
        log(f"  FineData extract exceptie: {e}", 'error')
    return None, None


# ─── eMAG Scraper ────────────────────────────────────────────────────────────
# Strategie DIRECTA (fara fallback pe pagina de search — cauza pretului gresit):
#   1. search-by-filters API (JSON intern eMAG) → URL produs → fetch → pret
#   2. suggest API → URL produs → fetch → pret
#   3. curl search → canonical /pd/ (redirect server) sau URL in script-uri → fetch → pret
#   4. Python requests fallback (acelasi flux, fara HTML search prices)
#   NICIODATA nu returnam prices din pagina de search (ar da produse gresite!)

_emag_warmed = False


def _emag_canonical_url(soup):
    """
    Returneaza URL-ul canonic al paginii (dupa redirect curl).
    eMAG pune <link rel='canonical' href='...'> sau <meta property='og:url'>.
    """
    tag = soup.find('link', rel='canonical')
    if tag:
        href = tag.get('href', '')
        if href.startswith('http'):
            return href
    tag = soup.find('meta', property='og:url')
    if tag:
        content = tag.get('content', '')
        if content.startswith('http'):
            return content
    return None


def _emag_best_pd_link(soup, code_lower):
    """
    Gaseste cel mai potrivit link /pd/ dintr-o pagina de cautare eMAG.
    Prioritate: link care contine codul produsului in URL.
    """
    pd_links = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/pd/' in href:
            full = href if href.startswith('http') else 'https://www.emag.ro' + href
            pd_links.append(full)

    if not pd_links:
        return None

    # Cauta variante ale codului in URL-uri
    code_parts = code_lower.replace('/', '').replace('-', '')
    variants = [code_lower, code_lower.replace('/', ''), code_parts]
    if '/' in code_lower:
        variants.append(code_lower.split('/')[0])

    # Prioritate 1: URL care contine codul produsului exact
    for href in pd_links:
        hl = href.lower()
        for v in variants:
            if v in hl:
                log(f"  eMAG: /pd/ link cu cod exact: {href[:80]}")
                return href

    # Prioritate 2: URL cu 'samsung' (evita produse nerelevante)
    for href in pd_links:
        if 'samsung' in href.lower():
            log(f"  eMAG: /pd/ link Samsung: {href[:80]}")
            return href

    # NU returnam primul link aleator - cauzeaza false positives
    log(f"  eMAG: {len(pd_links)} /pd/ links, niciunul nu corespunde codului")
    return None


def _emag_extract_price_from_product_page(soup, product_url):
    """
    Extrage pretul de pe o pagina de produs eMAG.
    Ordinea:
      1. JSON-LD (logging detaliat pentru debug)
      2. Meta tags (og:price, product:price)
      3. itemprop price
      4. CSS selectors specifice eMAG
      5. __NEXT_DATA__
      6. PRIMUL pret din corpul HTML fara script-uri (preful principal e INAINTEA
         produselor cross-sell in HTML → primul = corect, nu minimul = accesoriu)
    Returneaza (price, url) sau (None, None).
    ATENTIE: apeleaza NUMAI pe pagini de produs (/pd/), nu pe pagini de search!
    """
    # 1. JSON-LD — cu logging detaliat ca sa vedem de ce esueaza
    ld_scripts = soup.find_all('script', type='application/ld+json')
    log(f"  eMAG JSON-LD scripts gasite: {len(ld_scripts)}")
    for idx, sc in enumerate(ld_scripts[:5]):
        if sc.string:
            log(f"  eMAG JSON-LD[{idx}]: {sc.string[:250]}")
    p = extract_json_ld_price(soup)
    if p:
        log(f"  eMAG PRET GASIT (JSON-LD): {p}")
        return (p, product_url)

    # 2. Meta tags OpenGraph / product (specifice produsului curent)
    all_metas_with_price = [m for m in soup.find_all('meta')
                            if 'price' in str(m.get('property', '')).lower()
                            or 'price' in str(m.get('name', '')).lower()]
    log(f"  eMAG meta price tags: {[(m.get('property') or m.get('name'), m.get('content')) for m in all_metas_with_price]}")
    for prop_name in ['og:price:amount', 'product:price:amount', 'og:price']:
        meta = soup.find('meta', property=prop_name)
        if meta:
            val = meta.get('content', '')
            try:
                p = float(str(val).replace(',', '.').strip())
                if 100 < p < 300000:
                    log(f"  eMAG PRET GASIT (meta {prop_name}): {p}")
                    return (p, product_url)
            except (ValueError, TypeError):
                pass

    # 3. itemprop price (microdata)
    for elem in soup.find_all(attrs={'itemprop': 'price'})[:5]:
        val = (elem.get('content') or elem.get('data-price') or
               elem.get('data-value') or '')
        log(f"  eMAG itemprop price: content='{val}' text='{elem.get_text()[:30]}'")
        if not val:
            val = elem.get_text(separator='').strip()
        try:
            p = float(str(val).replace(',', '.').strip())
            if 100 < p < 300000:
                log(f"  eMAG PRET GASIT (itemprop): {p}")
                return (p, product_url)
        except (ValueError, TypeError):
            pass

    # 4. CSS selectors pentru pretul principal eMAG
    for sel in [
        'p.product-new-price',
        '.product-page-pricing p.product-new-price',
        '[data-zone="offer"] .product-new-price',
        '.product-new-price',
        '.price-new',
        '[data-testid="product-price"]',
        '[data-e2e="product-price"]',
    ]:
        elem = soup.select_one(sel)
        if not elem:
            continue
        val = elem.get('content') or elem.get('data-value') or ''
        if not val:
            val = elem.get_text(separator='').strip()
        log(f"  eMAG CSS '{sel}': '{val[:60]}'")
        p = parse_ro_price(val)
        if p and 100 < p < 300000:
            log(f"  eMAG PRET GASIT (CSS {sel}): {p}")
            return (p, product_url)
        # Fallback numeric pentru format fara virgula (ex: "1.39999Lei" → 1399.99)
        try:
            nums = re.findall(r'\d+', val)
            if len(nums) >= 2:
                combined = nums[0] + '.' + nums[1]
                p2 = float(combined)
                if 100 < p2 < 300000:
                    log(f"  eMAG PRET GASIT (CSS reconstruit {sel}): {p2}")
                    return (p2, product_url)
        except (ValueError, TypeError, IndexError):
            pass

    # 5. __NEXT_DATA__
    nd = soup.find('script', id='__NEXT_DATA__')
    if nd and nd.string:
        try:
            p = find_price_in_json(json.loads(nd.string))
            if p:
                log(f"  eMAG PRET GASIT (__NEXT_DATA__): {p}")
                return (p, product_url)
        except Exception:
            pass

    # 5b. GTM dataLayer / analytics scripts (eMAG injecteaza produsul + pretul
    #     in dataLayer.push la INCEPUTUL paginii — contine DOAR produsul principal,
    #     nu si cross-sell)
    for script in soup.find_all('script'):
        if not script.string:
            continue
        st = script.string
        # eMAG injecteaza ceva de forma: dataLayer.push({...,"price":"1399.99",...})
        # sau "ecommerce":{"detail":{"products":[{"price":1399.99}]}}
        if 'dataLayer' not in st and 'ecommerce' not in st:
            continue
        # Cauta "price": valoare numerica in context ecommerce/product
        for m in re.finditer(
            r'"price"\s*:\s*"?(\d{3,6}(?:[.,]\d{1,2})?)"?', st[:8000]
        ):
            try:
                raw = m.group(1).replace(',', '.')
                candidate = float(raw)
                if 400 < candidate < 300000:
                    log(f"  eMAG PRET GASIT (GTM dataLayer): {candidate}")
                    return (candidate, product_url)
            except (ValueError, TypeError):
                pass

    # 6. PRIMUL pret din corpul HTML fara script-uri
    # Rationament: pe pagina de produs eMAG, pretul principal apare INAINTE
    # de sectiunea cu produse cross-sell/recomandate.
    # → Primul pret in ordinea aparitiei = pretul produsului nostru.
    # → Minimul preturilor = pretul unui accesoriu de la sfarsitul paginii (BUG vechi)
    #
    # Metoda: eliminam tag-urile <script> si <style> din HTML brut, apoi
    # cautam primul pret valid in textul rezultat (in ordine de aparitie, nu sortat).
    try:
        html_raw = str(soup)
        # Scurtam la primii 300KB (sectiunea de produs e aproape de inceput)
        html_raw = html_raw[:300000]
        # Eliminam tot ce e in <script> si <style>
        html_no_scripts = re.sub(
            r'<(script|style)[^>]*>.*?</(script|style)>',
            ' ', html_raw, flags=re.DOTALL | re.IGNORECASE
        )
        # Gasim PRIMUL pret valid in ordine de aparitie
        for m in re.finditer(r'(\d{1,3}(?:\.\d{3})+,\d{2})', html_no_scripts):
            candidate = parse_ro_price(m.group(1))
            if candidate and 400 < candidate < 300000:
                log(f"  eMAG PRET GASIT (primul in HTML fara scripts): {candidate}")
                return (candidate, product_url)
        # Incearca si formatul fara separator mii (ex: 799,99 lei)
        for m in re.finditer(r'(\d{2,4},\d{2})\s*[Ll]ei', html_no_scripts):
            candidate = parse_ro_price(m.group(1))
            if candidate and 400 < candidate < 300000:
                log(f"  eMAG PRET GASIT (primul fara sep mii): {candidate}")
                return (candidate, product_url)
    except Exception as e:
        log(f"  eMAG eroare extragere HTML prim pret: {e}", 'warning')

    # 7. Ultima sansa: toate preturile sortate (poate include cross-sell)
    prices = find_prices_in_soup(soup)
    log(f"  eMAG preturi full page (ultima sansa): {prices[:8]}")
    if prices:
        return (prices[0], product_url)

    return (None, None)


def _emag_extract_product_url_from_json(data, code_lower, depth=0):
    """
    Cauta recursiv URL-ul produsului in JSON returnat de API-urile eMAG.
    Prioritizeaza URL-urile /pd/ care contin codul produsului.
    Returneaza (url_exact, url_fallback) — url_exact contine codul, url_fallback nu.
    """
    if depth > 8 or data is None:
        return None, None

    best_exact = None   # URL /pd/ care contine codul
    best_fallback = None  # URL /pd/ fara cod (primul gasit)

    if isinstance(data, dict):
        for key in ['url', 'link', 'href', 'product_url', 'productUrl', 'page_url',
                    'permalink', 'canonical_url']:
            val = data.get(key)
            if val and isinstance(val, str) and '/pd/' in val:
                full = val if val.startswith('http') else 'https://www.emag.ro' + val
                if code_lower in full.lower():
                    return full, full  # gasit exact — returneaza imediat
                if best_fallback is None:
                    best_fallback = full

        for v in list(data.values())[:25]:
            ex, fb = _emag_extract_product_url_from_json(v, code_lower, depth + 1)
            if ex:
                return ex, ex
            if fb and best_fallback is None:
                best_fallback = fb

    elif isinstance(data, list):
        for item in data[:15]:
            ex, fb = _emag_extract_product_url_from_json(item, code_lower, depth + 1)
            if ex:
                return ex, ex
            if fb and best_fallback is None:
                best_fallback = fb

    return best_exact, best_fallback


def scrape_emag(code):
    """
    Returneaza (price, source_url) sau (None, None).
    Flow DIRECT — cauta produsul via API JSON, nu colecta preturi din pagina de search.
    """
    global _emag_warmed
    log(f"\n--- eMAG ({code}) ---")
    code_lower = code.lower()

    # Warmup homepage (o singura data) pentru cookies realiste
    if not _emag_warmed:
        if IS_VERCEL:
            _emag_warmed = True  # Skip warmup pe Vercel
        else:
            try:
                wr = SESSION.get('https://www.emag.ro/', timeout=8)
                log(f"  eMAG warmup: {wr.status_code} ({len(wr.text):,}b)")
                _emag_warmed = True
            except Exception as e:
                log(f"  eMAG warmup eroare: {e}", 'warning')

    for variant in get_search_variants(code):
        v_enc = urllib.parse.quote(variant)
        search_url = f'https://www.emag.ro/search/{v_enc}'

        # ── METODA 1: search-by-filters API (JSON intern eMAG) ────────────────
        # Returneaza JSON cu produse + URL-uri. Nu necesita parsare HTML.
        try:
            ajax_url = (f'https://www.emag.ro/search-by-filters/list?source_id=4'
                        f'&s%5Bsearch_term%5D={v_enc}&lang=ro&_pb=1')
            r = SESSION.get(ajax_url, timeout=12, headers={
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': f'https://www.emag.ro/search/{v_enc}',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
            })
            log(f"  eMAG AJAX: {r.status_code} ({len(r.text):,}b)")
            if r.status_code == 200:
                txt = r.text.strip()
                if txt.startswith('{') or txt.startswith('['):
                    data = r.json()
                    log(f"  eMAG AJAX preview: {txt[:400]}")
                    # Cauta URL produs /pd/ in JSON (prioritate: URL cu codul exact)
                    prod_url_exact, prod_url_fb = _emag_extract_product_url_from_json(
                        data, code_lower)
                    prod_url = prod_url_exact or prod_url_fb
                    if prod_url:
                        log(f"  eMAG AJAX: URL produs: {prod_url[:80]}")
                        _, pp = get_page_curl(prod_url, timeout=12, referer=search_url)
                        if pp:
                            if not product_matches_code(pp, code):
                                log(f"  eMAG AJAX: produsul NU corespunde codului {code}, skip")
                            else:
                                p, url = _emag_extract_price_from_product_page(pp, prod_url)
                                if p:
                                    log(f"  eMAG PRET GASIT (AJAX → produs): {p}")
                                    return (p, url)
        except Exception as e:
            log(f"  eMAG AJAX eroare: {e}", 'warning')

        # ── METODA 2: Suggest API (JSON rapid, fara HTML) ─────────────────────
        for suggest_url in [
            f'https://www.emag.ro/suggest?q={v_enc}&lang=ro&limit=10',
            f'https://www.emag.ro/suggest/?q={v_enc}&lang=ro&limit=10',
        ]:
            try:
                r = SESSION.get(suggest_url, timeout=8, headers={
                    'Accept': 'application/json',
                    'Referer': 'https://www.emag.ro/',
                    'X-Requested-With': 'XMLHttpRequest',
                })
                log(f"  eMAG suggest: {r.status_code} ({len(r.text):,}b)")
                if r.status_code != 200:
                    continue
                txt = r.text.strip()
                if not (txt.startswith('[') or txt.startswith('{')):
                    continue
                data = r.json()
                items = data if isinstance(data, list) else data.get('suggestions', data.get('results', []))
                log(f"  eMAG suggest preview: {txt[:300]}")
                for item in (items or [])[:10]:
                    if not isinstance(item, dict):
                        continue
                    prod_url = (item.get('url') or item.get('link') or item.get('href') or '')
                    if prod_url and not prod_url.startswith('http'):
                        prod_url = 'https://www.emag.ro' + prod_url
                    if prod_url and '/pd/' in prod_url:
                        _, ps = get_page_curl(prod_url, timeout=12, referer='https://www.emag.ro/')
                        if ps:
                            if not product_matches_code(ps, code):
                                log(f"  eMAG suggest: produsul NU corespunde codului {code}, skip")
                                continue
                            p, url = _emag_extract_price_from_product_page(ps, prod_url)
                            if p:
                                log(f"  eMAG PRET GASIT (suggest → produs): {p}")
                                return (p, url)
                break  # Daca API-ul a raspuns JSON valid, nu mai incerca varianta 2
            except Exception as e:
                log(f"  eMAG suggest eroare: {e}", 'warning')

        # ── METODA 3: curl search → redirect /pd/ sau URL in script-uri ───────
        _, cs = get_page_curl(search_url, timeout=12, referer='https://www.emag.ro/')
        if cs:
            # Pasul A: verifica canonical — redirect server-side la pagina produs
            canonical = _emag_canonical_url(cs)
            log(f"  eMAG canonical URL: {canonical}")
            if canonical and '/pd/' in canonical:
                log(f"  eMAG: redirect la pagina produs detectat")
                if product_matches_code(cs, code):
                    p, url = _emag_extract_price_from_product_page(cs, canonical)
                    if p:
                        return (p, url)
                else:
                    log(f"  eMAG: redirect produs nu corespunde codului {code}, skip")

            # Pasul B: cauta URL /pd/ cu codul produsului in script-urile inline
            # (eMAG React embedeaza uneori date in window.__INITIAL_DATA__ etc.)
            for script in cs.find_all('script'):
                if not script.string:
                    continue
                script_text = script.string
                # Cauta URL-uri /pd/ care contin codul
                found_urls = re.findall(
                    r'https://www\.emag\.ro[^"\'\s<>]+/pd/[^"\'\s<>]+', script_text)
                for u in found_urls:
                    if code_lower in u.lower():
                        log(f"  eMAG: URL produs din script inline: {u[:80]}")
                        _, pp = get_page_curl(u, timeout=12, referer=search_url)
                        if pp:
                            p, url = _emag_extract_price_from_product_page(pp, u)
                            if p:
                                log(f"  eMAG PRET GASIT (script → produs): {p}")
                                return (p, url)

            # Pasul C: cauta link /pd/ in HTML (functioneaza daca React e pre-rendered)
            product_url = _emag_best_pd_link(cs, code_lower)
            log(f"  eMAG: cel mai bun /pd/ link: {product_url}")
            if product_url:
                _, pp = get_page_curl(product_url, timeout=12, referer=search_url)
                if pp:
                    if not product_matches_code(pp, code):
                        log(f"  eMAG Pasul C: produsul NU corespunde codului {code}, skip")
                    else:
                        p, url = _emag_extract_price_from_product_page(pp, product_url)
                        if p:
                            return (p, url)

        # ── METODA 4: Python requests fallback (acelasi flux, fara search prices) ──
        _, search_soup = get_page(search_url, referer='https://www.emag.ro/')
        if search_soup:
            canonical = _emag_canonical_url(search_soup)
            if canonical and '/pd/' in canonical:
                if product_matches_code(search_soup, code):
                    p, url = _emag_extract_price_from_product_page(search_soup, canonical)
                    if p:
                        log(f"  eMAG PRET GASIT (requests redirect produs): {p}")
                        return (p, url)
            product_url = _emag_best_pd_link(search_soup, code_lower)
            if product_url:
                _, pp = get_page(product_url, referer=search_url)
                if pp:
                    if not product_matches_code(pp, code):
                        log(f"  eMAG M4: produsul NU corespunde codului {code}, skip")
                    else:
                        p, url = _emag_extract_price_from_product_page(pp, product_url)
                        if p:
                            log(f"  eMAG PRET GASIT (requests produs): {p}")
                            return (p, url)
            # NOTA: find_prices_in_soup pe pagina de search NU se mai face niciodata
            # (returna 499.55 — cel mai ieftin produs de pe pagina, nu TV-ul nostru)

    log("  eMAG: negasit", 'warning')
    return (None, None)


# ─── Samsung Scraper ──────────────────────────────────────────────────────────
# Samsung foloseste Next.js - parsam __NEXT_DATA__ din pagina de cautare si produs

def _samsung_find_product_url_in_json(data, code_lower, depth=0):
    """Cauta recursiv URL-ul produsului Samsung in JSON (API/NEXT_DATA)."""
    if depth > 8 or data is None:
        return None
    if isinstance(data, dict):
        # Campuri URL comune in Samsung API
        for key in ['ctaUrl', 'url', 'productUrl', 'pdpUrl', 'link', 'href', 'productLink']:
            val = data.get(key)
            if val and isinstance(val, str) and code_lower in val.lower():
                if val.startswith('/') or val.startswith('http'):
                    url = val if val.startswith('http') else 'https://www.samsung.com' + val
                    return url
        for v in list(data.values())[:20]:
            r = _samsung_find_product_url_in_json(v, code_lower, depth + 1)
            if r:
                return r
    elif isinstance(data, list):
        for item in data[:10]:
            r = _samsung_find_product_url_in_json(item, code_lower, depth + 1)
            if r:
                return r
    return None


def _samsung_shop_api(code):
    """
    Samsung Shop API (tokocommerce) - returneaza (price, image_url, product_url) sau (None, None, None).
    Aceasta este metoda PRINCIPALA - returneaza pretul REAL de vanzare (nu PRP/list price).
    API: https://p1-smn3-api-cdn.shop.samsung.com/tokocommercewebservices/v2/ro/products/
    """
    SAMSUNG_API = 'https://p1-smn3-api-cdn.shop.samsung.com/tokocommercewebservices/v2/ro'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
    }

    # Pas 1: Gaseste SKU-ul complet din codul partial (ex: 55QN90F -> QE55QN90FATXXH)
    sku = None
    for variant in get_search_variants(code):
        try:
            r = SESSION.get(
                f'{SAMSUNG_API}/products/search',
                params={'query': variant, 'pageSize': '5', 'fields': 'code,name'},
                headers=headers, timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                products = data.get('products', [])
                if products:
                    # Cauta potrivirea exacta sau prima
                    for p in products:
                        p_code = p.get('code', '')
                        if code.upper() in p_code.upper() or variant.upper() in p_code.upper():
                            sku = p_code
                            break
                    if not sku:
                        sku = products[0].get('code')
                    if sku:
                        log(f"  Samsung API: SKU gasit: {sku} (din cautare '{variant}')")
                        break
        except Exception as e:
            log(f"  Samsung API search eroare: {e}")

    # Daca nu am gasit prin search, incearca direct cu codul
    if not sku:
        sku = code.upper()

    # Pas 2: Obtine detalii produs (pret real + imagine)
    try:
        r = SESSION.get(
            f'{SAMSUNG_API}/products/{sku}',
            params={'fields': 'FULL'},
            headers=headers, timeout=10
        )
        log(f"  Samsung API product: {r.status_code} pentru SKU={sku}")
        if r.status_code == 200:
            data = r.json()

            # Extrage pretul de vanzare din baseOptions[0].selected.priceData
            price = None
            image_url = None
            base_opts = data.get('baseOptions', [])
            if base_opts:
                selected = base_opts[0].get('selected', {})
                price_data = selected.get('priceData', {})
                price = price_data.get('value')

                # Extrage imaginea din galleryImagesV2
                gallery = selected.get('galleryImagesV2', [])
                if gallery:
                    images = gallery[0].get('images', [])
                    if images:
                        img_val = images[0].get('value', {})
                        raw_url = img_val.get('url', '')
                        if raw_url:
                            # Inlocuieste $ORIGIN_PNG$ cu un format rezonabil
                            image_url = raw_url.replace('$ORIGIN_PNG$', '$720_576_PNG$')
                            if '?' not in image_url:
                                image_url = raw_url

            # Fallback: pretul din root price
            if not price:
                root_price = data.get('price', {})
                price = root_price.get('value')

            # URL-ul produsului - folosim cautarea Samsung care functioneaza mereu
            product_url = f'https://www.samsung.com/ro/search/?searchvalue={urllib.parse.quote(sku)}'

            if price and price > 100:
                log(f"  Samsung API: pret={price}, imagine={image_url[:80] if image_url else 'N/A'}")
                return (price, image_url, product_url)
    except Exception as e:
        log(f"  Samsung API product eroare: {e}")

    return (None, None, None)


# Cache global pentru imaginea Samsung (populat de scrape_samsung)
_samsung_image_cache = {}


def scrape_samsung(code):
    """Returneaza (price, source_url) sau (None, None)."""
    global _samsung_image_cache
    log(f"\n--- Samsung ({code}) ---")
    code_lower = code.lower()

    # ── METODA PRINCIPALA: Samsung Shop API (tokocommerce) ────────────────
    # Returneaza pretul REAL de vanzare + imagine + URL produs
    api_price, api_image, api_url = _samsung_shop_api(code)
    if api_price:
        if api_image:
            _samsung_image_cache[code.upper()] = api_image
        log(f"  Samsung PRET GASIT (Shop API): {api_price}")
        return (api_price, api_url)

    # ── FALLBACK: JSON-LD de pe pagina produsului ─────────────────────────
    # Samsung.com/ro incarca preturile client-side (SDF templates).
    # JSON-LD contine pretul "list" (nu sale) dar e mai bun decat nimic.
    for variant in get_search_variants(code):
        variant_lower = variant.lower()
        v_enc = urllib.parse.quote(variant)
        search_url = f'https://www.samsung.com/ro/search/?searchvalue={v_enc}'

        _, cs = get_page_curl(search_url, timeout=12, referer='https://www.samsung.com/ro/')
        if not cs:
            continue

        # Gaseste URL-ul produsului din link-uri
        all_ro = [a['href'] for a in cs.find_all('a', href=True) if '/ro/' in a.get('href', '')]

        product_url = None
        for href in all_ro:
            hl = href.rstrip('/').lower()
            if hl.endswith(code_lower) or hl.endswith(variant_lower):
                if '?' not in href:
                    product_url = href if href.startswith('http') else 'https://www.samsung.com' + href
                    break
        if not product_url:
            for href in all_ro:
                if code_lower in href.lower() or variant_lower in href.lower():
                    if '?' not in href and '/all-' not in href:
                        product_url = href if href.startswith('http') else 'https://www.samsung.com' + href
                        break

        if product_url:
            _, pp = get_page_curl(product_url, timeout=12, referer=search_url)
            if pp:
                # JSON-LD — pretul "list" (poate fi diferit de pretul de vanzare)
                p = extract_json_ld_price(pp)
                if p:
                    log(f"  Samsung PRET GASIT (JSON-LD fallback): {p}")
                    # Extrage imagine din JSON-LD
                    for script in pp.find_all('script', type='application/ld+json'):
                        try:
                            ld = json.loads(script.string or '')
                            if isinstance(ld, dict) and 'Product' in str(ld.get('@type', '')):
                                img = ld.get('image', '')
                                if isinstance(img, str) and img.startswith('http'):
                                    _samsung_image_cache[code.upper()] = img
                                    break
                        except Exception:
                            pass
                    return (p, product_url)

                prices = find_prices_in_soup(pp)
                if prices:
                    log(f"  Samsung PRET GASIT (soup fallback): {prices[0]}")
                    return (prices[0], product_url)

    log("  Samsung: negasit", 'warning')
    return (None, None)


# ─── Flanco Scraper ───────────────────────────────────────────────────────────
# Flanco returneaza 403 fara cookies. Solutia: vizitam homepage-ul mai intai.

_flanco_warmed = False

def _is_flanco_product_url(href):
    """
    Filtreaza URL-urile Flanco: accepta doar pagini de produs, nu categorii/navigatie.
    Pagini de produs: /televizor-samsung-neo-qled-55qn90f-4k-138cm.html (slug lung, detaliat)
    Pagini de categorie: /tv-electronice-foto.html, /electrocasnice.html (slug scurt, generic)
    """
    if not href or '.html' not in href:
        return False
    hl = href.lower()
    # Trebuie sa fie de pe flanco.ro (sau relative path)
    if href.startswith('http') and 'flanco.ro/' not in hl:
        return False
    # Extrage doar slug-ul (ultima parte din URL, fara domeniu)
    slug = href.rstrip('/').split('/')[-1].replace('.html', '')
    # Paginile de produs au slug-uri lungi (>30 chars) cu detalii (brand, model, specificatii)
    if len(slug) < 25:
        return False
    # Categoriile au putine cuvinte separate de '-'
    parts = slug.split('-')
    if len(parts) < 4:
        return False
    # Exclude slug-uri cu pattern de categorie
    _category_patterns = ['electronice-foto', 'electrocasnice', 'telefoane-tablete',
                          'it-gaming', 'ingrijire-personala', 'casa-gradina']
    if any(p in slug.lower() for p in _category_patterns):
        return False
    return True


def scrape_flanco(code):
    """Returneaza (price, source_url) sau (None, None)."""
    global _flanco_warmed
    log(f"\n--- Flanco ({code}) ---")

    _flanco_exclude = ['telefon', 'phone', 'mobil', 'smartphone', 'smartwatch',
                       'tableta', 'laptop', 'notebook', 'casti', 'earbuds',
                       'aspirator', 'masina-de-spalat', 'kit']

    # ── FINEDATA FIRST (pe Vercel, curl poate fi blocat) ───────────────
    # Un singur apel FineData: search page, extrage product URL + pret din HTML
    if FINEDATA_API_KEY and IS_VERCEL:
        log("  Flanco: FineData prioritar pe Vercel...")
        variant = get_search_variants(code)[0]
        search_url = f'https://www.flanco.ro/catalogsearch/result/?q={urllib.parse.quote(variant)}'
        _, search_soup = _finedata_fetch(search_url)
        if search_soup:
            page_text_lower = search_soup.get_text().lower()
            if 'nu a gasit' not in page_text_lower and '0 produse' not in page_text_lower:
                # Cauta product URL
                product_url = None
                code_lower_f = code.lower()
                for a in search_soup.find_all('a', href=True):
                    href = a['href']
                    hl = href.lower()
                    if _is_flanco_product_url(href) and (code_lower_f in hl or variant.lower() in hl):
                        product_url = href if href.startswith('http') else 'https://www.flanco.ro' + href
                        break
                if not product_url:
                    for sel in ['a.product-item-link', '.product-item-name a']:
                        for a in search_soup.select(sel)[:5]:
                            href = a.get('href', '')
                            if _is_flanco_product_url(href):
                                if any(x in href.lower() for x in _flanco_exclude):
                                    continue
                                product_url = href if href.startswith('http') else 'https://www.flanco.ro' + href
                                break
                        if product_url:
                            break
                # Extrage pret din pagina de search (daca Flanco il afiseaza in listing)
                if product_url:
                    # Incearca pretul din search page (listing) fara al doilea apel
                    for sel in ['.special-price .price', '.price-box .price', '.price']:
                        for elem in search_soup.select(sel)[:5]:
                            p = parse_ro_price(elem.get_text(separator=''))
                            if p and p > 400:
                                log(f"  Flanco PRET GASIT (FineData search listing): {p}")
                                return (p, product_url)
                    p = extract_json_ld_price(search_soup)
                    if p:
                        log(f"  Flanco PRET GASIT (FineData search JSON-LD): {p}")
                        return (p, product_url)
                    # Fallback: al doilea apel FineData pt pagina produsului
                    log(f"  Flanco FineData product page: {product_url}")
                    _, prod_soup = _finedata_fetch(product_url)
                    if prod_soup and product_matches_code(prod_soup, code):
                        for sel in ['[data-price-type="finalPrice"] .price', '.special-price .price',
                                    '.price-wrapper .price', '.price-box .price', '.price']:
                            for elem in prod_soup.select(sel)[:3]:
                                p = parse_ro_price(elem.get_text(separator=''))
                                if p and p > 400:
                                    log(f"  Flanco PRET GASIT (FineData product): {p}")
                                    return (p, product_url)
                        p = extract_json_ld_price(prod_soup)
                        if p:
                            return (p, product_url)
                        prices = find_prices_in_soup(prod_soup)
                        if prices:
                            return (prices[0], product_url)
        log("  Flanco FineData: nu a gasit, continuam cu curl...")

    # Warmup cu curl (prima oara) — Flanco e blocat pentru Python/OpenSSL
    if not _flanco_warmed:
        if not IS_VERCEL:
            get_page_curl('https://www.flanco.ro/', timeout=8)
        _flanco_warmed = True

    for variant in get_search_variants(code):
        for search_url in [
            f'https://www.flanco.ro/catalogsearch/result/?q={urllib.parse.quote(variant)}',
            f'https://www.flanco.ro/search/?q={urllib.parse.quote(variant)}',
        ]:
            # Folosim curl.exe pentru bypass TLS fingerprint
            _, search_soup = get_page_curl(search_url, timeout=10, referer='https://www.flanco.ro/')
            if not search_soup:
                continue

            page_text_lower = search_soup.get_text().lower()
            if 'nu a gasit' in page_text_lower or '0 produse' in page_text_lower:
                log(f"  Flanco 0 rezultate pentru {variant}")
                continue

            product_url = None
            code_lower_f = code.lower()
            variant_lower_f = variant.lower()

            # Prioritate 1: URL care contine codul exact SI este pagina de produs
            for a in search_soup.find_all('a', href=True):
                href = a['href']
                hl = href.lower()
                # Exclude link-uri externe (Facebook, Twitter, etc.)
                if 'facebook.com' in hl or 'twitter.com' in hl or 'pinterest.com' in hl:
                    continue
                if _is_flanco_product_url(href) and (code_lower_f in hl or variant_lower_f in hl):
                    product_url = href if href.startswith('http') else 'https://www.flanco.ro' + href
                    break

            # Prioritate 2: CSS selectors specifice Magento product cards
            if not product_url:
                for sel in ['a.product-item-link', '.product-item-name a', '.product-name a',
                            '.product-item-info a', 'li.product-item a[href*=".html"]',
                            '.products-grid .product-item a[href*=".html"]']:
                    for a in search_soup.select(sel)[:5]:
                        href = a.get('href', '')
                        if _is_flanco_product_url(href):
                            hl = href.lower()
                            if any(x in hl for x in _flanco_exclude):
                                continue
                            product_url = href if href.startswith('http') else 'https://www.flanco.ro' + href
                            break
                    if product_url:
                        break

            # Prioritate 3: orice link de produs valid (slug lung) cu samsung
            if not product_url:
                for a in search_soup.find_all('a', href=True):
                    href = a['href']
                    hl = href.lower()
                    if not _is_flanco_product_url(href):
                        continue
                    if any(x in hl for x in _flanco_exclude):
                        continue
                    if 'samsung' in hl:
                        product_url = href if href.startswith('http') else 'https://www.flanco.ro' + href
                        break

            log(f"  Flanco product URL: {product_url}")

            if product_url:
                # Folosim curl si pentru pagina produs (Flanco blocheaza Python)
                _, prod_soup = get_page_curl(product_url, timeout=10, referer=search_url)
                if prod_soup:
                    # Validare: codul trebuie sa apara pe pagina produsului
                    if not product_matches_code(prod_soup, code):
                        log(f"  Flanco: produsul gasit NU corespunde codului {code}, skip")
                        continue
                    for sel in [
                        '[data-price-type="finalPrice"] .price',
                        '.special-price .price',
                        '.price-wrapper .price',
                        '.price-box .price',
                        '.price',
                    ]:
                        for elem in prod_soup.select(sel)[:3]:
                            text = elem.get_text(separator='')
                            p = parse_ro_price(text)
                            if p and p > 400:
                                log(f"  Flanco PRET GASIT ({sel}): {p}")
                                return (p, product_url)
                    p = extract_json_ld_price(prod_soup)
                    if p:
                        log(f"  Flanco PRET GASIT (JSON-LD): {p}")
                        return (p, product_url)
                    prices = find_prices_in_soup(prod_soup)
                    log(f"  Flanco preturi text: {prices[:8]}")
                    if prices:
                        return (prices[0], product_url)

    # ── DuckDuckGo fallback ──
    log("  Flanco: cautare via DuckDuckGo...")
    for variant in get_search_variants(code)[:2]:
        # Query mai larg - nu doar 'televizor', Flanco poate folosi alte sluguri
        for ddg_q in [
            f'site:flanco.ro samsung {variant}',
            f'site:flanco.ro {variant}',
        ]:
            ddg_url = f'https://html.duckduckgo.com/html/?q={urllib.parse.quote(ddg_q)}'
            _, ddg_soup = get_page_curl(ddg_url, timeout=8)
            if not ddg_soup:
                continue
            flanco_urls = []
            for a in ddg_soup.find_all('a', href=True):
                href = a['href']
                # DDG wraps URLs: //duckduckgo.com/l/?uddg=https%3A%2F%2F...
                if 'uddg=' in href:
                    m = re.search(r'uddg=([^&]+)', href)
                    if m:
                        href = urllib.parse.unquote(m.group(1))
                if 'flanco.ro/' in href and '.html' in href:
                    if _is_flanco_product_url(href):
                        if any(x in href.lower() for x in _flanco_exclude):
                            continue
                        if href not in flanco_urls:
                            flanco_urls.append(href)
            # Prioritizeaza URL-uri cu codul in ele
            code_lower_f = code.lower()
            flanco_urls.sort(key=lambda u: (0 if code_lower_f in u.lower() else 1))
            log(f"  DDG Flanco URLs: {flanco_urls[:3]}")
            for furl in flanco_urls[:3]:
                _, prod_soup = get_page_curl(furl, timeout=10, referer='https://www.flanco.ro/')
                if prod_soup and product_matches_code(prod_soup, code, strict=True):
                    for sel in [
                        '[data-price-type="finalPrice"] .price',
                        '.special-price .price',
                        '.price-wrapper .price',
                        '.price-box .price',
                        '.price',
                    ]:
                        for elem in prod_soup.select(sel)[:3]:
                            p = parse_ro_price(elem.get_text(separator=''))
                            if p and p > 400:
                                log(f"  Flanco PRET GASIT (DDG + {sel}): {p}")
                                return (p, furl)
                    p = extract_json_ld_price(prod_soup)
                    if p:
                        log(f"  Flanco PRET GASIT (DDG + JSON-LD): {p}")
                        return (p, furl)
                    prices = find_prices_in_soup(prod_soup)
                    if prices:
                        log(f"  Flanco PRET GASIT (DDG + text): {prices[0]}")
                        return (prices[0], furl)
            if flanco_urls:
                break  # am gasit URL-uri dar nu s-a potrivit codul

    log("  Flanco: negasit", 'warning')
    return (None, None)


# ─── Altex Scraper ────────────────────────────────────────────────────────────
# Altex este Next.js + Redux cu produse incarcate CLIENT-SIDE.
# Pagina de search are "ready": null in __NEXT_DATA__ → nu are produse in HTML.
# Strategia:
#   1. curl cu cookie jar (search page → cookies → API/data route cu cookies)
#   2. Parse __NEXT_DATA__ deep pentru product slugs/URLs
#   3. DuckDuckGo fallback pentru a gasi pagina de produs Altex
#   4. Pagina de produs direct (server-rendered cu JSON-LD)

_altex_cookie_file = None

def _get_altex_cookie_file():
    """Returneaza calea fisierului cookie Altex (temporary)."""
    global _altex_cookie_file
    if _altex_cookie_file is None:
        import tempfile
        _altex_cookie_file = os.path.join(tempfile.gettempdir(), 'altex_cookies.txt')
    return _altex_cookie_file


def _curl_with_cookies(url, timeout=None, referer=None, save_cookies=False):
    if timeout is None:
        timeout = CURL_TIMEOUT
    """
    curl.exe cu suport cookie jar. Altex necesita cookies intre requests.
    Returneaza (text, soup) sau (None, None).
    """
    cookie_file = _get_altex_cookie_file()
    cmd = [
        _get_curl_bin(), '-s', '-L',
        '-m', str(timeout),
        '--compressed',
        '-b', cookie_file,  # trimite cookies
        '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
        '-H', 'Accept: text/html,application/xhtml+xml,application/xml;'
              'q=0.9,image/avif,image/webp,*/*;q=0.8',
        '-H', 'Accept-Language: ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7',
        '-H', 'Sec-Fetch-Dest: document',
        '-H', 'Sec-Fetch-Mode: navigate',
        '-H', 'Sec-Fetch-Site: none',
        '-H', 'Sec-Fetch-User: ?1',
        '-H', 'DNT: 1',
    ]
    if _curl_supports_http2():
        cmd.insert(3, '--http2')
    if save_cookies:
        cmd += ['-c', cookie_file]  # salveaza cookies
    if referer:
        cmd += ['-H', f'Referer: {referer}', '-H', 'Sec-Fetch-Site: same-origin']
    cmd.append(url)

    try:
        run_kwargs = {'capture_output': True, 'timeout': timeout + 5}
        if IS_WINDOWS:
            run_kwargs['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
        result = subprocess.run(cmd, **run_kwargs)
        if result.returncode == 0 and result.stdout:
            text = result.stdout.decode('utf-8', errors='replace')
            size = len(text)
            log(f"  curl(cookie) {url[:65]} -> {size:,}b")
            if size > 500:
                return text, BeautifulSoup(text, 'html.parser')
            log(f"  curl(cookie): raspuns prea mic ({size}b)", 'warning')
        else:
            err = result.stderr.decode('utf-8', errors='replace')[:150]
            log(f"  curl(cookie) EROARE (exit {result.returncode}): {err}", 'warning')
    except subprocess.TimeoutExpired:
        log(f"  curl(cookie) TIMEOUT ({timeout}s): {url[:60]}", 'warning')
    except FileNotFoundError:
        log("  curl.exe negasit pe sistem, fallback requests", 'warning')
    except Exception as e:
        log(f"  curl(cookie) exceptie: {e}, fallback requests", 'error')
    # ── Fallback: requests Session ──
    try:
        hdrs = dict(HEADERS)
        if referer:
            hdrs['Referer'] = referer
            hdrs['Sec-Fetch-Site'] = 'same-origin'
        resp = SESSION.get(url, timeout=timeout, allow_redirects=True, headers=hdrs)
        size = len(resp.text)
        log(f"  requests(cookie-fallback) {url[:65]} -> {resp.status_code} ({size:,}b)")
        if resp.status_code == 200 and size > 500:
            return resp.text, BeautifulSoup(resp.text, 'html.parser')
    except Exception as e2:
        log(f"  requests(cookie-fallback) eroare: {e2}", 'warning')
    return None, None


def _curl_json_with_cookies(url, timeout=12, referer=None):
    """
    curl.exe cu Accept: application/json si cookies.
    Returneaza parsed JSON sau None.
    """
    cookie_file = _get_altex_cookie_file()
    cmd = [
        _get_curl_bin(), '-s', '-L',
        '-m', str(timeout),
        '--compressed',
        '-b', cookie_file,
        '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
        '-H', 'Accept: application/json, text/javascript, */*; q=0.01',
        '-H', 'Accept-Language: ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7',
        '-H', 'X-Requested-With: XMLHttpRequest',
        '-H', 'Sec-Fetch-Dest: empty',
        '-H', 'Sec-Fetch-Mode: cors',
        '-H', 'Sec-Fetch-Site: same-origin',
    ]
    if _curl_supports_http2():
        cmd.insert(3, '--http2')
    if referer:
        cmd += ['-H', f'Referer: {referer}']
    cmd.append(url)

    try:
        run_kwargs = {'capture_output': True, 'timeout': timeout + 5}
        if IS_WINDOWS:
            run_kwargs['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
        result = subprocess.run(cmd, **run_kwargs)
        if result.returncode == 0 and result.stdout:
            text = result.stdout.decode('utf-8', errors='replace').strip()
            log(f"  curl(json) {url[:65]} -> {len(text):,}b")
            if text and (text[0] in '{['):
                return json.loads(text)
            log(f"  curl(json): nu e JSON (incepe cu '{text[:20]}')", 'warning')
    except subprocess.TimeoutExpired:
        log(f"  curl(json) TIMEOUT: {url[:60]}", 'warning')
    except json.JSONDecodeError as e:
        log(f"  curl(json) JSON decode: {e}", 'warning')
    except Exception as e:
        log(f"  curl(json) exceptie: {e}", 'error')
    return None


def _altex_find_product_url_in_json(data, code_lower, depth=0):
    """Cauta recursiv URL-ul produsului Altex in JSON. Prioritate: /cpd/ cu cod."""
    if depth > 10 or data is None:
        return None
    best_cpd = None  # URL /cpd/ fara cod (fallback)
    if isinstance(data, dict):
        for key in ['url', 'slug', 'href', 'link', 'productUrl', 'path',
                    'canonical', 'page_url', 'seoUrl', 'product_url']:
            val = data.get(key)
            if val and isinstance(val, str):
                vl = val.lower()
                if '/cpd/' in vl or '-cpd-' in vl:
                    full = val if val.startswith('http') else 'https://altex.ro' + val
                    if code_lower in vl:
                        return full  # URL exact cu cod
                    if best_cpd is None:
                        best_cpd = full
        for v in list(data.values())[:30]:
            r = _altex_find_product_url_in_json(v, code_lower, depth + 1)
            if r:
                if code_lower in r.lower():
                    return r
                if best_cpd is None:
                    best_cpd = r
    elif isinstance(data, list):
        for item in data[:20]:
            r = _altex_find_product_url_in_json(item, code_lower, depth + 1)
            if r:
                if code_lower in r.lower():
                    return r
                if best_cpd is None:
                    best_cpd = r
    return best_cpd


def _altex_find_product_slugs_in_html(html_text, code_lower):
    """
    Cauta URL-uri /cpd/ in intregul HTML (inclusiv in script-uri JS inline).
    Altex poate avea slug-uri de produs in analytics, GTM, sau JS bundles.
    Formatul Altex: /product-name/cpd/CODE/ sau /product-name-cpd-CODE/
    """
    slugs = []
    # Pattern 1: /product-name/cpd/CODE/ (formatul actual Altex)
    for m in re.finditer(r'(/[a-z0-9-]+/cpd/[A-Za-z0-9]+/?)', html_text):
        slug = m.group(1)
        if slug not in slugs:
            slugs.append(slug)
    # Pattern 2: /slug-cu-cpd-ALTCODE/ (format vechi)
    for m in re.finditer(r'(/[a-z0-9-]+-cpd-[A-Z0-9]+/?)', html_text):
        slug = m.group(1)
        if slug not in slugs:
            slugs.append(slug)
    # Pattern 3: in JSON strings - "url":"/slug/cpd/..." sau "url":"/slug-cpd-..."
    for m in re.finditer(r'"(?:url|slug|href|path|link|urlAsPath|canonical|seoUrl)"\s*:\s*"([^"]*(?:/cpd/|cpd)[^"]*)"',
                         html_text, re.IGNORECASE):
        slug = m.group(1)
        if slug.startswith('/') and slug not in slugs:
            slugs.append(slug)
    # Filtreaza si prioritizeaza
    exact = [s for s in slugs if code_lower in s.lower()]
    tv = [s for s in slugs if 'televizor' in s.lower() or 'tv' in s.lower()]
    others = [s for s in slugs if s not in exact and s not in tv]
    return exact + tv + others


def _altex_extract_price_from_product_page(soup, product_url):
    """
    Extrage pretul de pe o pagina de produs Altex.
    Paginile de produs Altex sunt server-rendered (spre deosebire de search).
    """
    # 1. __NEXT_DATA__
    nd = soup.find('script', id='__NEXT_DATA__')
    if nd and nd.string:
        try:
            nd_data = json.loads(nd.string)
            log(f"  Altex produs NEXT_DATA preview: {nd.string[:400]}")
            p = find_price_in_json(nd_data)
            if p:
                log(f"  Altex PRET GASIT (produs __NEXT_DATA__): {p}")
                return p
        except Exception:
            pass

    # 2. JSON-LD
    p = extract_json_ld_price(soup)
    if p:
        log(f"  Altex PRET GASIT (produs JSON-LD): {p}")
        return p

    # 3. Meta tags
    for prop_name in ['og:price:amount', 'product:price:amount', 'og:price']:
        meta = soup.find('meta', property=prop_name)
        if meta:
            val = meta.get('content', '')
            try:
                p = float(str(val).replace(',', '.').strip())
                if 100 < p < 300000:
                    log(f"  Altex PRET GASIT (meta {prop_name}): {p}")
                    return p
            except (ValueError, TypeError):
                pass

    # 4. itemprop price
    for elem in soup.find_all(attrs={'itemprop': 'price'})[:5]:
        val = (elem.get('content') or elem.get('data-price')
               or elem.get_text(separator=''))
        p = parse_ro_price(str(val))
        if p and p > 400:
            log(f"  Altex PRET GASIT (itemprop): {p}")
            return p

    # 5. CSS selectors specifice Altex — PRETUL DE VANZARE (cel verde/mare)
    # IMPORTANT: Altex afiseaza 2 preturi: vechi (strikethrough) si de vanzare (verde).
    # Trebuie sa luam pretul de VANZARE, nu pe cel vechi!
    # Strategia: cautam INTAI selectori de pret SPECIAL/SALE, apoi pret curent.
    # Evitam selectori de pret vechi/old/regular.
    sale_selectors = [
        # Altex specific - pretul de vanzare (verde, mare)
        '.Price--current .Price-int',
        '.Price--current',
        '.special-price .price',
        '.special-price',
        '.price-new',
        '.product-new-price',
        '[data-testid="product-price"]',
        '[data-testid="special-price"]',
        # Generic - pretul activ
        '.product-page-price .active-price',
        '.price-box .price-final_price .price',
    ]
    for sel in sale_selectors:
        elem = soup.select_one(sel)
        if not elem:
            continue
        # Verifica sa NU fie in container de pret vechi
        parent_classes = ' '.join(elem.parent.get('class', [])) if elem.parent else ''
        if 'old' in parent_classes.lower() or 'regular' in parent_classes.lower():
            continue
        val = elem.get('data-price') or elem.get('content') or ''
        if not val:
            val = elem.get_text(separator='').strip()
        p = parse_ro_price(str(val))
        if p and 400 < p < 300000:
            log(f"  Altex PRET GASIT (CSS sale {sel}): {p}")
            return p

    # Fallback: [data-price] dar NU din container de pret vechi
    for elem in soup.select('[data-price]')[:5]:
        parent_classes = ' '.join(elem.parent.get('class', [])) if elem.parent else ''
        if 'old' in parent_classes.lower() or 'regular' in parent_classes.lower():
            continue
        val = elem.get('data-price', '')
        p = parse_ro_price(str(val))
        if p and 400 < p < 300000:
            log(f"  Altex PRET GASIT (data-price): {p}")
            return p

    # 6. GTM dataLayer / analytics — cauta specialPrice/salePrice INAINTE de price
    for script in soup.find_all('script'):
        if not script.string:
            continue
        st = script.string
        if 'dataLayer' not in st and 'ecommerce' not in st and 'product' not in st.lower():
            continue
        # Prioritate: specialPrice, salePrice, discountedPrice
        for price_key in ['specialPrice', 'salePrice', 'discountedPrice',
                          'promoPrice', 'activePrice', 'finalPrice']:
            m = re.search(
                rf'"{price_key}"\s*:\s*"?(\d{{3,6}}(?:[.,]\d{{1,2}})?)"?', st[:8000])
            if m:
                try:
                    raw = m.group(1).replace(',', '.')
                    candidate = float(raw)
                    if 400 < candidate < 300000:
                        log(f"  Altex PRET GASIT (GTM {price_key}): {candidate}")
                        return candidate
                except (ValueError, TypeError):
                    pass
        # Fallback: "price" generic (dar e posibil sa fie pretul vechi)
        for m in re.finditer(
            r'"price"\s*:\s*"?(\d{3,6}(?:[.,]\d{1,2})?)"?', st[:8000]
        ):
            try:
                raw = m.group(1).replace(',', '.')
                candidate = float(raw)
                if 400 < candidate < 300000:
                    log(f"  Altex PRET GASIT (GTM price): {candidate}")
                    return candidate
            except (ValueError, TypeError):
                pass

    # 7. Pretul cel mai mic din HTML fara scripts (pretul de vanzare e mereu <= pretul vechi)
    # IMPORTANT: pe Altex pretul de vanzare e MAI MIC decat cel vechi,
    # deci minimul preturilor = pretul corect de vanzare
    prices = find_prices_in_soup(soup, min_price=400)
    log(f"  Altex preturi produs text: {prices[:6]}")
    if prices:
        # Returneaza MINIMUL (pretul de vanzare, nu cel vechi strikethrough)
        return min(prices)

    return None


def _extract_altex_cpd_urls(soup, html_text, code):
    """Extrage URL-uri /cpd/ din soup sau HTML, prioritizand codul cautat."""
    code_lower = code.lower()
    altex_urls = []
    if soup:
        for a in soup.find_all('a', href=True):
            href = a['href']
            # DDG wraps URLs: //duckduckgo.com/l/?uddg=https%3A%2F%2Faltex.ro%2F...
            if 'uddg=' in href:
                m = re.search(r'uddg=([^&]+)', href)
                if m:
                    decoded = urllib.parse.unquote(m.group(1))
                    if 'altex.ro' in decoded.lower():
                        altex_urls.append(decoded)
            # Google wraps: /url?q=https://altex.ro/...&sa=U&...
            elif '/url?q=' in href or '/url?sa=' in href:
                m = re.search(r'[?&]q=([^&]+)', href)
                if m:
                    decoded = urllib.parse.unquote(m.group(1))
                    if 'altex.ro' in decoded.lower():
                        altex_urls.append(decoded)
            elif 'altex.ro' in href.lower():
                altex_urls.append(href)
    # Cauta URL-uri Altex /cpd/ direct in textul HTML (ex: in scripturi, date JSON)
    if html_text:
        for m in re.finditer(
            r'https?://(?:www\.)?altex\.ro/[^\s"\'<>]*?/cpd/[^\s"\'<>]+', html_text
        ):
            url = m.group(0).rstrip('.,;)')
            altex_urls.append(url)
    # Prioritate: URL-uri /cpd/
    for url in altex_urls:
        if '/cpd/' in url.lower() and code_lower in url.lower():
            return url
    for url in altex_urls:
        if '/cpd/' in url.lower() and 'televizor' in url.lower():
            return url
    for url in altex_urls:
        if '/cpd/' in url.lower():
            return url
    return None


def _altex_search_duckduckgo(code):
    """
    Cauta pe DuckDuckGo si Bing URL-ul produsului Altex.
    """
    for variant in get_search_variants(code)[:2]:
        query = f'site:altex.ro televizor samsung {variant}'
        q_enc = urllib.parse.quote(query)

        # Incearca DuckDuckGo
        ddg_url = f'https://html.duckduckgo.com/html/?q={q_enc}'
        text, soup = get_page_curl(ddg_url, timeout=10, referer='https://duckduckgo.com/')
        if text and len(text) > 20000:  # >20KB = raspuns real (nu pagina anti-bot)
            log(f"  DDG search: {len(text):,}b")
            url = _extract_altex_cpd_urls(soup, text, code)
            if url:
                log(f"  DDG Altex URL: {url}")
                return url
            log(f"  DDG: fara URL-uri Altex")
        else:
            log(f"  DDG blocat ({len(text) if text else 0}b), incerc Bing...")

        # Fallback: Bing
        bing_url = f'https://www.bing.com/search?q={q_enc}&count=10'
        text2, soup2 = get_page_curl(bing_url, timeout=12, referer='https://www.bing.com/')
        if text2 and len(text2) > 20000:
            log(f"  Bing search: {len(text2):,}b")
            url = _extract_altex_cpd_urls(soup2, text2, code)
            if url:
                log(f"  Bing Altex URL: {url}")
                return url
            log(f"  Bing: fara URL-uri Altex")
        else:
            log(f"  Bing blocat ({len(text2) if text2 else 0}b), incerc Google...")

        # Fallback: Google
        google_url = f'https://www.google.com/search?q={q_enc}&num=10'
        text3, soup3 = get_page_curl(
            google_url, timeout=12, referer='https://www.google.com/')
        if text3 and len(text3) > 20000:
            log(f"  Google search: {len(text3):,}b")
            url = _extract_altex_cpd_urls(soup3, text3, code)
            if url:
                log(f"  Google Altex URL: {url}")
                return url
            log(f"  Google: fara URL-uri Altex")
        else:
            log(f"  Google blocat ({len(text3) if text3 else 0}b)")

    return None


def scrape_altex(code):
    """
    Returneaza (price, source_url) sau (None, None).
    Strategie: URL direct /produs/cpd/CODE/ (Altex accepta orice slug),
    fallback search page + DuckDuckGo.
    """
    log(f"\n--- Altex ({code}) ---")
    code_lower = code.lower()
    skip_to_ddg = False  # Flag: skip steps 2-5 daca ready=null (client-side only)

    # ── FINEDATA FIRST (pe Vercel, curl e blocat de Akamai) ────────────
    # UN SINGUR apel FineData: search page cu JS render + AI extraction
    if FINEDATA_API_KEY and IS_VERCEL:
        log("  Altex: FineData JS render pe Vercel...")
        variant = get_search_variants(code)[0]
        search_url = f'https://altex.ro/cauta/?q={urllib.parse.quote(variant)}'
        price, prod_url = _finedata_extract_price(
            search_url, code, js_render=True, timeout=40)
        if price:
            if prod_url and not prod_url.startswith('http'):
                prod_url = 'https://altex.ro' + prod_url
            log(f"  Altex PRET GASIT (FineData AI): {price}")
            return (price, prod_url or search_url)
        # Pe Vercel, curl nu merge pt Altex (Akamai) - skip
        log("  Altex: skip curl pe Vercel (blocat de Akamai)")
        return (None, None)

    # ── STEP 0: URL direct cu /produs/cpd/CODE/ ─────────────────────────
    # Altex accepta orice slug inainte de /cpd/ si serveste pagina produsului
    # cu server-side rendering (pretul e in HTML, nu client-side ca search page)
    # Codurile cu / (ex: HW-B400F/EN) trebuie incercate si fara sufix
    altex_codes = [code]
    if '/' in code:
        altex_codes.append(code.split('/')[0])  # ex: HW-B400F
    for altex_code in altex_codes:
        direct_url = f'https://altex.ro/produs/cpd/{urllib.parse.quote(altex_code, safe="")}/'
        log(f"  Altex STEP 0: URL direct {direct_url}")
        _, direct_soup = _curl_with_cookies(
            direct_url, timeout=20, referer='https://altex.ro/', save_cookies=True)
        if direct_soup:
            if product_matches_code(direct_soup, code):
                price = _altex_extract_price_from_product_page(direct_soup, direct_url)
                if price:
                    log(f"  Altex PRET GASIT (URL direct): {price}")
                    return (price, direct_url)
                # Try generic price extraction
                p = extract_json_ld_price(direct_soup)
                if p:
                    log(f"  Altex PRET GASIT (URL direct JSON-LD): {p}")
                    return (p, direct_url)
                prices = find_prices_in_soup(direct_soup)
                if prices:
                    log(f"  Altex PRET GASIT (URL direct soup): {prices[0]}")
                    return (prices[0], direct_url)
            else:
                log(f"  Altex STEP 0: cod nu corespunde, skip")
        else:
            log(f"  Altex STEP 0: pagina nu s-a incarcat")

    for variant in get_search_variants(code):
        variant_lower = variant.lower()
        v_enc = urllib.parse.quote(variant)
        search_url = f'https://altex.ro/cauta/?q={v_enc}'

        # ── STEP 1: Fetch search page cu cookie jar ──────────────────────────
        # Salveaza cookies de la Altex (necesare pentru API calls ulterioare)
        search_text, search_soup = _curl_with_cookies(
            search_url, timeout=20, referer='https://altex.ro/', save_cookies=True)

        build_id = None
        if search_soup:
            nd = search_soup.find('script', id='__NEXT_DATA__')
            if nd and nd.string:
                try:
                    nd_data = json.loads(nd.string)
                    build_id = nd_data.get('buildId', '')
                    page_props = nd_data.get('props', {}).get('pageProps', {})
                    ready_val = page_props.get('ready')
                    log(f"  Altex __NEXT_DATA__ buildId: {build_id}")
                    log(f"  Altex __NEXT_DATA__ ready: {ready_val}")

                    # Verifica daca __NEXT_DATA__ are deja produse (ready != null)
                    if ready_val is not None:
                        log(f"  Altex: ready != null, caut produse in __NEXT_DATA__")
                        prod_url = _altex_find_product_url_in_json(nd_data, code_lower)
                        p = find_price_in_json(nd_data)
                        if p:
                            log(f"  Altex PRET GASIT (__NEXT_DATA__ search): {p}")
                            return (p, prod_url or search_url)
                        if prod_url and '/cpd/' in prod_url:
                            log(f"  Altex: URL produs din NEXT_DATA: {prod_url}")
                            _, pp = _curl_with_cookies(
                                prod_url, timeout=15, referer=search_url)
                            if pp:
                                price = _altex_extract_price_from_product_page(pp, prod_url)
                                if price:
                                    return (price, prod_url)
                    else:
                        # ready=null: Altex nu trimite produse server-side
                        # Steps 2-5 vor esua (API-uri returneaza HTML, nu JSON)
                        # Sarim direct la DuckDuckGo + pagina de produs directa
                        log(f"  Altex: ready=null, skip steps 2-5 (client-side only)")
                        skip_to_ddg = True
                except Exception as e:
                    log(f"  Altex __NEXT_DATA__ parse: {e}", 'warning')

        # ── STEP 1.5: Cauta link-uri /cpd/ direct in HTML-ul search page ────
        # Chiar daca ready=null, HTML-ul poate contine link-uri de produs
        if search_soup:
            product_url = None
            for a in search_soup.find_all('a', href=True):
                href = a['href']
                hl = href.lower()
                if '/cpd/' in hl:
                    if code_lower in hl or variant_lower in hl:
                        product_url = (href if href.startswith('http')
                                       else 'https://altex.ro' + href)
                        break
            if not product_url:
                for a in search_soup.find_all('a', href=True):
                    href = a['href']
                    hl = href.lower()
                    if '/cpd/' in hl and not any(
                            x in hl for x in ['telefon', 'phone', 'laptop', 'tableta']):
                        product_url = (href if href.startswith('http')
                                       else 'https://altex.ro' + href)
                        break
            if product_url:
                log(f"  Altex product URL (HTML direct): {product_url}")
                _, prod_soup = _curl_with_cookies(
                    product_url, timeout=15, referer=search_url)
                if prod_soup:
                    if product_matches_code(prod_soup, code):
                        price = _altex_extract_price_from_product_page(prod_soup, product_url)
                        if price:
                            return (price, product_url)
                    else:
                        log(f"  Altex: produsul gasit NU corespunde codului {code}, skip")

        # ── STEP 1.6: Cauta slugs /cpd/ in intregul HTML (regex) ─────────────
        if search_text:
            slugs = _altex_find_product_slugs_in_html(search_text, code_lower)
            if slugs:
                log(f"  Altex slugs in HTML: {slugs[:5]}")
                for slug in slugs[:3]:
                    prod_url = slug if slug.startswith('http') else 'https://altex.ro' + slug
                    _, pp = _curl_with_cookies(
                        prod_url, timeout=15, referer=search_url)
                    if pp:
                        if product_matches_code(pp, code):
                            price = _altex_extract_price_from_product_page(pp, prod_url)
                            if price:
                                return (price, prod_url)

        if skip_to_ddg:
            # ready=null: skip STEP 2 (Next.js data route) dar incearca STEP 3 (API direct)
            # STEP 3: API interne Altex - nu depind de ready state
            for api_url in [
                f'https://altex.ro/api/v2/catalog/products?q={v_enc}&size=10',
                f'https://altex.ro/api/v2/search?q={v_enc}&size=10',
                f'https://altex.ro/api/2.0/product/search/?q={v_enc}&size=10',
                f'https://altex.ro/api/1.0/catalog/search?q={v_enc}&size=10',
            ]:
                api_data = _curl_json_with_cookies(
                    api_url, timeout=12, referer=search_url)
                if api_data:
                    log(f"  Altex API (skip_to_ddg) JSON OK: {str(api_data)[:300]}")
                    prod_url = _altex_find_product_url_in_json(api_data, code_lower)
                    p = find_price_in_json(api_data)
                    if p:
                        log(f"  Altex PRET GASIT (API skip_to_ddg): {p}")
                        return (p, prod_url or search_url)
                    if prod_url and '/cpd/' in prod_url:
                        full_url = prod_url if prod_url.startswith('http') else 'https://altex.ro' + prod_url
                        _, pp = _curl_with_cookies(full_url, timeout=15, referer=search_url)
                        if pp:
                            price = _altex_extract_price_from_product_page(pp, full_url)
                            if price:
                                return (price, full_url)
            break  # goto DuckDuckGo

        # ── STEP 2: Next.js data route CU cookies ────────────────────────────
        # Cu cookies din step 1, data route ar trebui sa returneze JSON
        if build_id:
            for data_url in [
                f'https://altex.ro/_next/data/{build_id}/cauta/{v_enc}.json',
                f'https://altex.ro/_next/data/{build_id}/cauta/{v_enc}.json?filters={v_enc}',
            ]:
                nj_data = _curl_json_with_cookies(
                    data_url, timeout=15, referer=search_url)
                if nj_data:
                    log(f"  Altex Next.js data: JSON OK")
                    prod_url = _altex_find_product_url_in_json(nj_data, code_lower)
                    p = find_price_in_json(nj_data)
                    if p:
                        log(f"  Altex PRET GASIT (NJ data JSON): {p}")
                        return (p, prod_url or search_url)
                    if prod_url and '/cpd/' in prod_url:
                        full_url = prod_url if prod_url.startswith('http') else 'https://altex.ro' + prod_url
                        log(f"  Altex NJ data: URL produs: {full_url}")
                        _, pp = _curl_with_cookies(
                            full_url, timeout=15, referer=search_url)
                        if pp:
                            price = _altex_extract_price_from_product_page(pp, full_url)
                            if price:
                                return (price, full_url)
                    break  # daca am primit JSON, nu incerca a 2-a varianta

        # ── STEP 3: API interne Altex CU cookies ─────────────────────────────
        for api_url in [
            f'https://altex.ro/api/v2/catalog/products?q={v_enc}&size=10',
            f'https://altex.ro/api/v2/search?q={v_enc}&size=10',
            f'https://altex.ro/api/2.0/product/search/?q={v_enc}&size=10',
            f'https://altex.ro/api/1.0/catalog/search?q={v_enc}&size=10',
        ]:
            api_data = _curl_json_with_cookies(
                api_url, timeout=12, referer=search_url)
            if api_data:
                log(f"  Altex API JSON OK: {str(api_data)[:300]}")
                prod_url = _altex_find_product_url_in_json(api_data, code_lower)
                p = find_price_in_json(api_data)
                if p:
                    log(f"  Altex PRET GASIT (API): {p}")
                    return (p, prod_url or search_url)
                if prod_url and '/cpd/' in prod_url:
                    full_url = prod_url if prod_url.startswith('http') else 'https://altex.ro' + prod_url
                    _, pp = _curl_with_cookies(
                        full_url, timeout=15, referer=search_url)
                    if pp:
                        price = _altex_extract_price_from_product_page(pp, full_url)
                        if price:
                            return (price, full_url)

        break  # nu incerca alte variante daca am obtinut HTML

    # ── DuckDuckGo fallback ──────────────────────────────────────────────
    log("  Altex: cautare via DuckDuckGo...")
    ddg_url = _altex_search_duckduckgo(code)
    if ddg_url:
        log(f"  Altex DDG URL: {ddg_url}")
        _, pp = _curl_with_cookies(ddg_url, timeout=15, referer='https://duckduckgo.com/')
        if pp:
            if not product_matches_code(pp, code, strict=True):
                log(f"  Altex DDG: produsul NU corespunde codului {code}, skip")
            else:
                price = _altex_extract_price_from_product_page(pp, ddg_url)
                if price:
                    return (price, ddg_url)

    log("  Altex: negasit", 'warning')
    return (None, None)


# ─── Imagine Produs ──────────────────────────────────────────────────────────

def get_product_image(code):
    """Obtine imaginea produsului - foloseste cache-ul Samsung (populat de scrape_samsung)."""
    log(f"\n--- Image ({code}) ---")

    # ── 1. Cache Samsung (populat de _samsung_shop_api in scrape_samsung) ─────
    cached = _samsung_image_cache.get(code.upper())
    if cached:
        log(f"  [Image] Samsung cache: {cached[:80]}")
        return cached

    # ── 2. Samsung Shop API direct (daca cache-ul e gol) ─────────────────────
    _, api_image, _ = _samsung_shop_api(code)
    if api_image:
        log(f"  [Image] Samsung API: {api_image[:80]}")
        return api_image

    # ── 3. JSON-LD de pe pagina produsului Samsung ───────────────────────────
    code_lower = code.lower()
    for variant in get_search_variants(code)[:1]:
        v_enc = urllib.parse.quote(variant)
        search_url = f'https://www.samsung.com/ro/search/?searchvalue={v_enc}'
        _, cs = get_page_curl(search_url, timeout=10, referer='https://www.samsung.com/ro/')
        if not cs:
            continue
        # Cauta link produs
        for a in cs.find_all('a', href=True):
            href = a['href']
            if '/ro/' in href and code_lower in href.lower() and '?' not in href:
                prod_url = href if href.startswith('http') else 'https://www.samsung.com' + href
                _, pp = get_page_curl(prod_url, timeout=10, referer=search_url)
                if pp:
                    for script in pp.find_all('script', type='application/ld+json'):
                        try:
                            ld = json.loads(script.string or '')
                            if isinstance(ld, dict) and 'Product' in str(ld.get('@type', '')):
                                img = ld.get('image', '')
                                if isinstance(img, str) and 'samsung.com' in img:
                                    log(f"  [Image] Samsung JSON-LD: {img[:80]}")
                                    return img
                        except Exception:
                            pass
                break

    # ── 4. Fallback eMAG ─────────────────────────────────────────────────────
    for variant in get_search_variants(code)[:1]:
        _, soup = get_page(f'https://www.emag.ro/search/{urllib.parse.quote(variant)}')
        if soup:
            for sel in ['.card-item img', '.thumbnail-wrapper img',
                        'article img', '.product-image img']:
                img_el = soup.select_one(sel)
                if img_el:
                    src = (img_el.get('src') or img_el.get('data-src') or
                           img_el.get('data-lazy', '').split()[0])
                    if src and src.startswith('http') and 'logo' not in src.lower():
                        log(f"  [Image] eMAG fallback: {src[:70]}")
                        return src

    # ── 5. Fallback Altex ───────────────────────────────────────────────────
    for variant in get_search_variants(code)[:1]:
        _, soup = get_page(f'https://altex.ro/cauta/?q={urllib.parse.quote(variant)}')
        if soup:
            for sel in ['img.product-image', '.product-thumb img', '.product-item img',
                        'article img', '.a-m-product-card img']:
                img_el = soup.select_one(sel)
                if img_el:
                    src = (img_el.get('src') or img_el.get('data-src') or '')
                    if src and src.startswith('http') and 'logo' not in src.lower():
                        log(f"  [Image] Altex fallback: {src[:70]}")
                        return src

    # ── 6. Fallback Flanco ──────────────────────────────────────────────────
    for variant in get_search_variants(code)[:1]:
        _, soup = get_page(f'https://www.flanco.ro/catalogsearch/result/?q={urllib.parse.quote(variant)}')
        if soup:
            for sel in ['.product-item img', '.product-image img', 'img.product-image-photo']:
                img_el = soup.select_one(sel)
                if img_el:
                    src = (img_el.get('src') or img_el.get('data-src') or '')
                    if src and src.startswith('http') and 'logo' not in src.lower() and 'placeholder' not in src.lower():
                        log(f"  [Image] Flanco fallback: {src[:70]}")
                        return src

    # ── 7. Fallback: placeholder cu codul produsului ────────────────────────
    placeholder = f'https://placehold.co/400x300/1e1b4b/c4b5fd?text={urllib.parse.quote(code)}&font=roboto'
    log(f"  [Image] Placeholder: {placeholder[:70]}")
    return placeholder


# ─── Specificatii Samsung ─────────────────────────────────────────────────────

def get_samsung_specs(code):
    """
    Extrage specificatiile tehnice de pe pagina Samsung.com/ro.
    Returneaza dict cu sectiuni si items, sau None daca nu gaseste.
    Format: {'sections': [{'name': 'Ecran', 'items': [{'title': 'Rezolutie', 'value': '4K'}]}]}
    """
    log(f"\n--- Samsung Specs ({code}) ---")
    code_lower = code.lower()

    # Pas 1: Gaseste URL-ul paginii produsului via Samsung Search API
    # Samsung search API returneaza pdpUrl direct (nu necesita scraping HTML)
    product_url = None
    spec_timeout = max(CURL_TIMEOUT, 12)  # min 12s pentru specs
    try:
        api_url = (f'https://searchapi.samsung.com/v6/front/b2c/product/card/detail/global'
                   f'?siteCode=ro&modelList={urllib.parse.quote(code)}'
                   f'&commonCodeYN=N&saleSkuYN=N&onlyRequestSkuYN=N&keySummaryYN=Y')
        import urllib.request as urlreq
        req = urlreq.Request(api_url, headers={
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urlreq.urlopen(req, timeout=12) as resp:
            api_data = json.loads(resp.read())
            models = (api_data.get('response', {}).get('resultData', {})
                      .get('productList', [{}])[0].get('modelList', []))
            if models:
                pdp = models[0].get('pdpUrl', '')
                if pdp and '/ro/' in pdp:
                    product_url = 'https://www.samsung.com' + pdp if not pdp.startswith('http') else pdp
                    log(f"  Specs: PDP URL din API: {product_url[:80]}")
    except Exception as e:
        log(f"  Specs: Samsung API eroare: {e}", 'warning')

    # Fallback: cauta URL din pagina de search cu curl
    if not product_url:
        for variant in get_search_variants(code)[:2]:
            v_enc = urllib.parse.quote(variant)
            search_url = f'https://www.samsung.com/ro/search/?searchvalue={v_enc}'
            _, cs = get_page_curl(search_url, timeout=spec_timeout, referer='https://www.samsung.com/ro/')
            if not cs:
                continue

            all_ro = [a['href'] for a in cs.find_all('a', href=True) if '/ro/' in a.get('href', '')]
            variant_lower = variant.lower()
            for href in all_ro:
                hl = href.rstrip('/').lower()
                if hl.endswith(code_lower) or hl.endswith(variant_lower):
                    if '?' not in href:
                        product_url = href if href.startswith('http') else 'https://www.samsung.com' + href
                        break
            if not product_url:
                for href in all_ro:
                    if (code_lower in href.lower() or variant_lower in href.lower()) and '?' not in href and '/all-' not in href:
                        product_url = href if href.startswith('http') else 'https://www.samsung.com' + href
                        break
            if product_url:
                break

    if not product_url:
        log("  Specs: pagina produsului negasita")
        return None

    log(f"  Specs: pagina gasita: {product_url[:80]}")

    # Pas 2: Descarca pagina si extrage specificatiile
    _, soup = get_page_curl(product_url, timeout=spec_timeout, referer='https://www.samsung.com/ro/')
    if not soup:
        return None

    html = str(soup)

    # ── Metoda 1: Accordion + CSS classes (formatul clasic Samsung) ──────
    section_names = re.findall(r'an-la="accordion:([^"]+)"', html)

    # Incearca multiple pattern-uri pentru spec items (Samsung schimba clasele periodic)
    spec_patterns = [
        # Pattern clasic
        re.compile(
            r'<p\s+class="pdd32-product-spec__content-item-title">\s*([^<]+?)\s*</p>'
            r'\s*(?:<p\s+class="pdd32-product-spec__content-item-desc">\s*([^<]*?)\s*</p>)?',
            re.S
        ),
        # Pattern alternativ (class partial match)
        re.compile(
            r'<[a-z]+\s+class="[^"]*spec[^"]*item-title[^"]*"[^>]*>\s*([^<]+?)\s*</[a-z]+>'
            r'\s*(?:<[a-z]+\s+class="[^"]*spec[^"]*item-(?:desc|value)[^"]*"[^>]*>\s*([^<]*?)\s*</[a-z]+>)?',
            re.S
        ),
        # Pattern generic Samsung (dt/dd sau div-uri cu spec)
        re.compile(
            r'<dt[^>]*class="[^"]*spec[^"]*"[^>]*>\s*([^<]+?)\s*</dt>'
            r'\s*<dd[^>]*>\s*([^<]*?)\s*</dd>',
            re.S
        ),
    ]

    all_items = []
    for pat in spec_patterns:
        all_items = pat.findall(html)
        if all_items:
            break

    # ── Metoda 2: BeautifulSoup fallback (daca regex nu gaseste) ─────────
    if not all_items:
        # Cauta spec items prin BeautifulSoup (mai robust la schimbari HTML)
        spec_containers = soup.find_all(class_=re.compile(r'spec.*item|product-spec'))
        for container in spec_containers:
            title_el = container.find(class_=re.compile(r'title|label|name|key'))
            value_el = container.find(class_=re.compile(r'desc|value|data'))
            if title_el:
                t = title_el.get_text(strip=True)
                v = value_el.get_text(strip=True) if value_el else ''
                if t:
                    all_items.append((t, v))

    # ── Metoda 3: JSON-LD / structurata din pagina Samsung ───────────────
    if not all_items:
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                ld = json.loads(script.string or '')
                items_list = ld if isinstance(ld, list) else [ld]
                for item in items_list:
                    if not isinstance(item, dict):
                        continue
                    if 'Product' not in str(item.get('@type', '')):
                        continue
                    # additionalProperty contine specificatii
                    for prop in item.get('additionalProperty', []):
                        if isinstance(prop, dict) and prop.get('name'):
                            all_items.append((prop['name'], str(prop.get('value', ''))))
            except Exception:
                continue

    if not all_items:
        log("  Specs: nu am gasit items")
        return None

    # ── Extrage sectiuni (daca exista) ───────────────────────────────────
    if not section_names:
        # Fallback: cauta alte pattern-uri de sectiuni
        section_names = re.findall(r'data-section[^"]*"([^"]+)"', html)
    if not section_names:
        section_names = re.findall(r'class="[^"]*spec[^"]*section[^"]*header[^"]*"[^>]*>([^<]+)', html)

    if section_names:
        sections = []
        seen_sections = set()
        for name in section_names:
            clean_name = name.replace('&amp;', '&').strip()
            if clean_name and clean_name not in seen_sections:
                seen_sections.add(clean_name)
                sections.append({'name': clean_name, 'items': []})

        # Assign items to sections based on position in HTML
        section_positions = []
        for name in sections:
            raw_name = name['name'].replace('&', '&amp;')
            pos = html.find(f'accordion:{raw_name}"')
            if pos < 0:
                pos = html.find(f'"{raw_name}"')
            if pos < 0:
                pos = html.find(raw_name)
            section_positions.append(pos if pos >= 0 else 999999999)

        item_positions = []
        search_start = 0
        for title, value in all_items:
            pos = html.find(title.strip() if isinstance(title, str) else str(title), search_start)
            item_positions.append(pos)
            if pos >= 0:
                search_start = pos + 1

        for idx, (title, value) in enumerate(all_items):
            item_pos = item_positions[idx]
            assigned = len(sections) - 1
            for si in range(len(sections)):
                next_pos = section_positions[si + 1] if si + 1 < len(sections) else 999999999
                if section_positions[si] <= item_pos < next_pos:
                    assigned = si
                    break
            title_clean = title.strip() if isinstance(title, str) else str(title)
            value_clean = value.strip().replace('&quot;', '"').replace('&amp;', '&') if value else ''
            if title_clean:
                sections[assigned]['items'].append({'title': title_clean, 'value': value_clean})

        sections = [s for s in sections if s['items']]
    else:
        # Fara sectiuni: pune totul intr-o singura sectiune
        sections = [{'name': 'Specificatii', 'items': []}]
        for title, value in all_items:
            title_clean = title.strip() if isinstance(title, str) else str(title)
            value_clean = value.strip().replace('&quot;', '"').replace('&amp;', '&') if value else ''
            if title_clean:
                sections[0]['items'].append({'title': title_clean, 'value': value_clean})

    log(f"  Specs: {len(sections)} sectiuni, {sum(len(s['items']) for s in sections)} items total")
    return {'sections': sections}


# ─── Functii noi Altex: API intern + Sitemap ─────────────────────────────────
# Descoperite prin reverse engineering JavaScript Altex (webpack bundle).
# Nu modifica nicio functie existenta.

# Cache global sitemap Altex (incarcat o singura data per instanta)
_altex_sitemap_cache = {}

def _altex_scrape_via_api(code):
    """
    NOUA: Cauta pretul Altex prin API-ul intern descoperit din bundle-ul JS.
    Endpoint: https://lcdn.altex.ro/api/v2/catalog/search/{term}?size=48
    Returneaza (price, url) sau (None, None).
    """
    code_lower = code.lower()
    for variant in get_search_variants(code)[:3]:
        v_enc = urllib.parse.quote(variant)
        api_url = f'https://lcdn.altex.ro/api/v2/catalog/search/{v_enc}?size=48'
        referer = f'https://altex.ro/cauta/?q={v_enc}'
        log(f"  Altex API intern: {api_url}")

        # Incearca cu headers identice cu cele din browser
        cookie_file = _get_altex_cookie_file()
        cmd = [
            _get_curl_bin(), '-s', '-L', '-m', '12', '--compressed',
            '-b', cookie_file,
            '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
            '-H', 'Accept: application/json, text/plain, */*',
            '-H', 'Accept-Language: ro-RO,ro;q=0.9',
            '-H', f'Referer: {referer}',
            '-H', 'Origin: https://altex.ro',
            '-H', 'X-Requested-With: XMLHttpRequest',
            api_url,
        ]
        try:
            run_kwargs = {'capture_output': True, 'timeout': 17}
            if IS_WINDOWS:
                run_kwargs['creationflags'] = 0x08000000
            result = subprocess.run(cmd, **run_kwargs)
            if result.returncode == 0 and result.stdout:
                text = result.stdout.decode('utf-8', errors='replace').strip()
                log(f"  Altex API intern: {len(text)}b | {text[:100]}")
                if text and text not in ('[]', '{}', ''):
                    try:
                        data = json.loads(text)
                        prod_url = _altex_find_product_url_in_json(data, code_lower)
                        p = find_price_in_json(data)
                        if p:
                            log(f"  Altex API intern PRET: {p}")
                            return (p, prod_url or api_url)
                        if prod_url and '/cpd/' in prod_url:
                            full_url = prod_url if prod_url.startswith('http') else 'https://altex.ro' + prod_url
                            _, pp = _curl_with_cookies(full_url, timeout=15, referer=api_url)
                            if pp:
                                price = _altex_extract_price_from_product_page(pp, full_url)
                                if price:
                                    return (price, full_url)
                    except (json.JSONDecodeError, Exception) as e:
                        log(f"  Altex API intern parse eroare: {e}")
        except Exception as e:
            log(f"  Altex API intern curl exceptie: {e}")

        # ── Fallback: requests direct pe API-ul CDN Altex ──
        try:
            api_hdrs = {
                'User-Agent': HEADERS['User-Agent'],
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'ro-RO,ro;q=0.9',
                'Referer': referer,
                'Origin': 'https://altex.ro',
                'X-Requested-With': 'XMLHttpRequest',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-site',
                'Sec-Ch-Ua': HEADERS.get('Sec-Ch-Ua', ''),
                'Sec-Ch-Ua-Mobile': '?0',
                'Sec-Ch-Ua-Platform': '"Windows"',
            }
            resp = requests.get(api_url, headers=api_hdrs, timeout=15)
            log(f"  Altex API requests fallback: {resp.status_code} {len(resp.text)}b")
            if resp.status_code == 200 and resp.text.strip() and resp.text.strip()[0] in '{[':
                data = resp.json()
                prod_url = _altex_find_product_url_in_json(data, code_lower)
                p = find_price_in_json(data)
                if p:
                    log(f"  Altex API requests PRET: {p}")
                    return (p, prod_url or api_url)
                if prod_url and '/cpd/' in prod_url:
                    full_url = prod_url if prod_url.startswith('http') else 'https://altex.ro' + prod_url
                    _, pp = _curl_with_cookies(full_url, timeout=15, referer=api_url)
                    if pp:
                        price = _altex_extract_price_from_product_page(pp, full_url)
                        if price:
                            return (price, full_url)
        except Exception as e2:
            log(f"  Altex API requests fallback eroare: {e2}")

    return (None, None)


def _altex_scrape_via_sitemap(code):
    """
    NOUA: Cauta URL-ul produsului in sitemap-ul TV Altex, apoi scrapeaza pretul.
    Sitemap: altex.ro/sitemaps/products/altex_tv_audio_video_foto_sitemap.xml
    Accesibil deoarece robots.txt e public si sitemaps sunt indexate de Google.
    Returneaza (price, url) sau (None, None).
    """
    global _altex_sitemap_cache
    code_lower = code.lower()

    sitemap_urls = [
        'https://altex.ro/sitemaps/products/altex_tv_audio_video_foto_sitemap.xml',
        'https://altex.ro/sitemaps/search/altex_ugs.xml',
    ]

    for sm_url in sitemap_urls:
        # Foloseste cache daca sitemap-ul a fost deja incarcat
        if sm_url not in _altex_sitemap_cache:
            log(f"  Altex sitemap: descarcare {sm_url}")
            sm_text, _ = get_page_curl(sm_url, timeout=25)
            if sm_text and len(sm_text) > 1000:
                _altex_sitemap_cache[sm_url] = sm_text
                log(f"  Altex sitemap: {len(sm_text):,}b incarcat")
            else:
                _altex_sitemap_cache[sm_url] = ''
                log(f"  Altex sitemap: inaccesibil ({len(sm_text) if sm_text else 0}b)")
                continue
        else:
            sm_text = _altex_sitemap_cache[sm_url]

        if not sm_text:
            continue

        # Cauta URL-uri /cpd/ care contin codul produsului
        for variant in get_search_variants(code):
            v_lower = variant.lower()
            for m in re.finditer(
                r'<loc>(https://altex\.ro[^<]+/cpd/[^<]+)</loc>', sm_text, re.IGNORECASE
            ):
                url = m.group(1)
                if v_lower in url.lower():
                    log(f"  Altex sitemap URL gasit: {url}")
                    _, prod_soup = _curl_with_cookies(url, timeout=15, referer=sm_url)
                    if prod_soup:
                        if product_matches_code(prod_soup, code):
                            price = _altex_extract_price_from_product_page(prod_soup, url)
                            if price:
                                log(f"  Altex sitemap PRET: {price}")
                                return (price, url)
                        else:
                            log(f"  Altex sitemap: cod nu corespunde, skip")

    return (None, None)


# ─── Functie suplimentara noua: agregatori romani de preturi ─────────────────
# Apelata DUPA ce scraperul principal a esuat (price=None).
# Nu modifica nicio functie existenta.

def scrape_vendor_supplementary(code, vendor):
    """
    Cauta pretul unui vendor prin:
    - Pentru Altex: API intern lcdn + Sitemap TV (metode noi)
    - Toti vendorii: agregatori romani (preturi.ro, shopmania.ro, compari.ro)
    Returneaza (price, url) sau (None, None).
    """
    log(f"\n--- Supplementary ({vendor}/{code}) ---")

    # ── Altex: API intern + Sitemap (metode noi, specifice Altex) ────────────
    if vendor == 'altex':
        # Incearca API-ul intern Altex (lcdn.altex.ro/api/v2/catalog/search/)
        price, url = _altex_scrape_via_api(code)
        if price:
            return (price, url)

        # Incearca sitemap-ul TV Altex (lista completa produse cu URL /cpd/)
        price, url = _altex_scrape_via_sitemap(code)
        if price:
            return (price, url)

    for variant in get_search_variants(code)[:2]:
        v_enc = urllib.parse.quote(variant)

        # ── preturi.ro ───────────────────────────────────────────────────────
        for search_url in [
            f'https://www.preturi.ro/search/?q={v_enc}',
            f'https://www.preturi.ro/search/?keywords={v_enc}',
        ]:
            _, soup = get_page_curl(
                search_url, timeout=12, referer='https://www.preturi.ro/')
            if not soup:
                continue
            log(f"  Preturi.ro search OK: {search_url[-60:]}")

            prices = extract_vendor_prices_from_page(soup)
            if vendor in prices:
                log(f"  Preturi.ro {vendor} (search direct): {prices[vendor]}")
                return (prices[vendor], search_url)

            product_url = None
            for a in soup.find_all('a', href=True):
                href = a['href']
                hl = href.lower()
                if variant.lower() in hl or code.lower() in hl:
                    product_url = href if href.startswith('http') else 'https://www.preturi.ro' + href
                    break
            if not product_url:
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if any(p in href.lower() for p in ['/produs/', '/televizor', '/p/']):
                        product_url = href if href.startswith('http') else 'https://www.preturi.ro' + href
                        break
            if product_url:
                log(f"  Preturi.ro product URL: {product_url[:80]}")
                _, prod_soup = get_page_curl(
                    product_url, timeout=12, referer=search_url)
                if prod_soup:
                    prices = extract_vendor_prices_from_page(prod_soup)
                    if vendor in prices:
                        log(f"  Preturi.ro {vendor}: {prices[vendor]}")
                        return (prices[vendor], product_url)
                    log(f"  Preturi.ro: niciun pret pentru {vendor}")
            break  # o singura varianta per agregator

        # ── shopmania.ro ─────────────────────────────────────────────────────
        for search_url in [
            f'https://www.shopmania.ro/cumpara/-{v_enc}',
            f'https://www.shopmania.ro/cauta/?q={v_enc}',
        ]:
            _, soup = get_page_curl(
                search_url, timeout=12, referer='https://www.shopmania.ro/')
            if not soup:
                continue
            log(f"  ShopMania search OK: {search_url[-60:]}")

            prices = extract_vendor_prices_from_page(soup)
            if vendor in prices:
                log(f"  ShopMania {vendor} (search direct): {prices[vendor]}")
                return (prices[vendor], search_url)

            product_url = None
            for a in soup.find_all('a', href=True):
                href = a['href']
                if variant.lower() in href.lower() or code.lower() in href.lower():
                    product_url = href if href.startswith('http') else 'https://www.shopmania.ro' + href
                    break
            if not product_url:
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if any(p in href.lower() for p in ['/produs/', '/televizor', '/p/']):
                        product_url = href if href.startswith('http') else 'https://www.shopmania.ro' + href
                        break
            if product_url:
                log(f"  ShopMania product URL: {product_url[:80]}")
                _, prod_soup = get_page_curl(
                    product_url, timeout=12, referer=search_url)
                if prod_soup:
                    prices = extract_vendor_prices_from_page(prod_soup)
                    if vendor in prices:
                        log(f"  ShopMania {vendor}: {prices[vendor]}")
                        return (prices[vendor], product_url)
                    log(f"  ShopMania: niciun pret pentru {vendor}")
            break

        # ── compari.ro (extins cu link direct catre produs) ──────────────────
        for search_url in [
            f'https://www.compari.ro/search/?keywords={v_enc}',
            f'https://www.compari.ro/search/?q={v_enc}',
        ]:
            _, soup = get_page_curl(
                search_url, timeout=12, referer='https://www.compari.ro/')
            if not soup:
                continue
            log(f"  Compari search OK: {search_url[-60:]}")

            prices = extract_vendor_prices_from_page(soup)
            if vendor in prices:
                log(f"  Compari {vendor} (search direct): {prices[vendor]}")
                return (prices[vendor], search_url)

            product_url = None
            for a in soup.find_all('a', href=True):
                href = a['href']
                if variant.lower() in href.lower() or code.lower() in href.lower():
                    product_url = href if href.startswith('http') else 'https://www.compari.ro' + href
                    break
            if not product_url:
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if any(p in href.lower() for p in ['/produs/', '/p/', '/product/', '/pret/']):
                        product_url = href if href.startswith('http') else 'https://www.compari.ro' + href
                        break
            if product_url:
                log(f"  Compari product URL: {product_url[:80]}")
                _, prod_soup = get_page_curl(
                    product_url, timeout=12, referer=search_url)
                if prod_soup:
                    prices = extract_vendor_prices_from_page(prod_soup)
                    if vendor in prices:
                        log(f"  Compari {vendor}: {prices[vendor]}")
                        return (prices[vendor], product_url)
                    log(f"  Compari: niciun pret pentru {vendor}")
            break

    log(f"  Supplementary: negasit {vendor}", 'warning')
    return (None, None)


# ─── Cautare per vendor (rapid, sub 10s) ─────────────────────────────────────

def search_single_vendor(code, vendor):
    """Cauta pretul pe un singur vendor. Rapid, sub 10s."""
    import urllib.parse
    code = code.strip().upper()
    vendor = vendor.lower()

    products = load_products()
    kuziini_price = None
    category = ''
    if code in products:
        kuziini_price = products[code]['price']
        category = products[code]['category']

    vendor_funcs = {
        'samsung': scrape_samsung,
        'emag': scrape_emag,
        'flanco': scrape_flanco,
        'altex': scrape_altex,
    }

    search_urls = {
        'samsung': f'https://www.samsung.com/ro/search/?searchvalue={urllib.parse.quote(code)}',
        'emag': f'https://www.emag.ro/search/{urllib.parse.quote(code)}',
        'flanco': f'https://www.flanco.ro/catalogsearch/result/?q={urllib.parse.quote(code)}',
        'altex': f'https://altex.ro/cauta/?q={urllib.parse.quote(code)}',
    }

    if vendor not in vendor_funcs:
        return {'error': f'Vendor necunoscut: {vendor}'}

    func = vendor_funcs[vendor]
    try:
        result = func(code)
        price = result[0] if result else None
        url = result[1] if result else None
        image_url = None
        if vendor == 'samsung' and result and len(result) > 2:
            image_url = result[2]
    except Exception as e:
        log(f"  {vendor} eroare: {e}")
        price = None
        url = None
        image_url = None

    # Fallback suplimentar: daca scraperul principal nu a gasit pretul,
    # incearca agregatorii romani (preturi.ro, shopmania.ro, compari.ro)
    if price is None:
        try:
            sup_price, sup_url = scrape_vendor_supplementary(code, vendor)
            if sup_price:
                price = sup_price
                url = sup_url or url
        except Exception as e:
            log(f"  {vendor} supplementary eroare: {e}")

    return {
        'code': code,
        'vendor': vendor,
        'category': category,
        'kuziini_price': round(kuziini_price, 2) if kuziini_price else None,
        'price': round(price, 2) if price else None,
        'url': url or search_urls[vendor],
        'image_url': image_url,
    }


# ─── Functie principala de cautare (folosita de Flask handler) ────────────────

def search_product(code, cron_mode=False):
    """
    Cauta preturile pentru un cod de produs Samsung.
    cron_mode=True: timeout-uri mai mari (cron are 60s, nu 10s).
    Returneaza dict cu rezultatele.
    """
    import concurrent.futures

    code = code.strip().upper()
    if not code:
        return {'error': 'Codul produsului este gol.'}

    log(f"\n{'='*55}")
    log(f"  Cautare: {code}")

    # Kuziini din Excel
    products = load_products()
    kuziini_price = None
    category = ''
    if code in products:
        kuziini_price = products[code]['price']
        category = products[code]['category']

    # Scrape in paralel
    results = {}
    result_urls = {}
    image_result = [None]

    # Timeout per-vendor: cron=45s, user request=9s, local=120s
    if cron_mode:
        VENDOR_TIMEOUT = 45
    elif IS_VERCEL:
        VENDOR_TIMEOUT = 20
    else:
        VENDOR_TIMEOUT = 120

    vendor_search_urls = {
        'samsung': f'https://www.samsung.com/ro/search/?searchvalue={urllib.parse.quote(code)}',
        'emag':    f'https://www.emag.ro/search/{urllib.parse.quote(code)}',
        'flanco':  f'https://www.flanco.ro/catalogsearch/result/?q={urllib.parse.quote(code)}',
        'altex':   f'https://altex.ro/cauta/?q={urllib.parse.quote(code)}',
    }

    # Cron: skip Altex/Flanco pe Vercel (FineData prea lent pt batch, 40s/vendor)
    # User requests: toate vendorii (FineData incape in 60s pt 1 vendor)
    skip_vendors = []
    if cron_mode and IS_VERCEL:
        skip_vendors = ['altex', 'flanco']
        log(f"  CRON: skip {skip_vendors} (FineData prea lent pt batch)")

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        fut_aggregator = ex.submit(scrape_price_aggregator, code)
        futs = {}
        for v, func in [('samsung', scrape_samsung), ('emag', scrape_emag),
                         ('flanco', scrape_flanco), ('altex', scrape_altex)]:
            if v not in skip_vendors:
                futs[v] = ex.submit(func, code)

        try:
            aggregator_prices = fut_aggregator.result(timeout=VENDOR_TIMEOUT)
        except Exception:
            aggregator_prices = {}

        for k, f in futs.items():
            try:
                result = f.result(timeout=VENDOR_TIMEOUT)
                ind_price = result[0] if result else None
                ind_url   = result[1] if result else None
            except Exception:
                ind_price = None
                ind_url = None
                log(f"  {k}: TIMEOUT ({VENDOR_TIMEOUT}s)")
            if ind_price is not None:
                results[k] = ind_price
                result_urls[k] = ind_url or vendor_search_urls[k]
            elif aggregator_prices.get(k):
                results[k] = aggregator_prices[k]
                result_urls[k] = vendor_search_urls[k]
            else:
                results[k] = None
                result_urls[k] = vendor_search_urls[k]

        # Imagine: dupa scrape_samsung (populeaza _samsung_image_cache)
        try:
            image_result[0] = ex.submit(get_product_image, code).result(timeout=VENDOR_TIMEOUT)
        except Exception:
            image_result[0] = None

    # Sanity check: elimina preturi aberante (prea mici fata de celelalte = produs gresit)
    valid_prices = [v for v in results.values() if v and v > 0]
    if len(valid_prices) >= 2:
        median_price = sorted(valid_prices)[len(valid_prices) // 2]
        for k in list(results.keys()):
            if results[k] and results[k] < median_price * 0.15:
                log(f"  SANITY: {k} pret {results[k]} e prea mic vs median {median_price}, marcat indisponibil")
                results[k] = None

    return {
        'code': code,
        'category': category,
        'kuziini_price': round(kuziini_price, 2) if kuziini_price else None,
        'image_url': image_result[0],
        'prices': {k: (round(v, 2) if v else None) for k, v in results.items()},
        'urls': result_urls,
    }
