"""
Vercel Serverless Function - Kuziini Price Search API
Handles: /api/search?code=XXX, /api/version, /api/reload_excel
"""
from flask import Flask, request, jsonify
import time

app = Flask(__name__)

# Import scraper module (same directory)
from api.scraper import search_product, load_products, APP_VERSION, warmup_session

# Warmup session once on cold start
warmup_session()

_start_time = time.time()


@app.route('/api/search', methods=['GET'])
def api_search():
    code = request.args.get('code', '').strip().upper()
    if not code:
        return jsonify({'error': 'Codul produsului este gol.'}), 400
    result = search_product(code)
    resp = jsonify(result)
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp


@app.route('/api/version', methods=['GET'])
def api_version():
    resp = jsonify({'version': _start_time, 'app_version': APP_VERSION})
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp


@app.route('/api/reload_excel', methods=['GET'])
def api_reload():
    from api.scraper import _products_cache
    import api.scraper as scraper_mod
    scraper_mod._products_cache = None
    products = load_products()
    resp = jsonify({'ok': True, 'count': len(products)})
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp
