# Kuziini Preturi - Comparator Preturi Samsung Romania

> Aplicatie web PWA (Progressive Web App) pentru compararea preturilor produselor Samsung pe piata din Romania, cu scraping automat zilnic, cache Redis si rapoarte de miscare preturi.

---

## Cuprins

1. [Prezentare Generala](#prezentare-generala)
2. [Arhitectura](#arhitectura)
3. [Stack Tehnologic](#stack-tehnologic)
4. [Platforme si Integrari](#platforme-si-integrari)
5. [Structura Proiect](#structura-proiect)
6. [Endpoints API](#endpoints-api)
7. [Sistem Cron - Scraping Automat](#sistem-cron---scraping-automat)
8. [Vendori - Metode Scraping](#vendori---metode-scraping)
9. [Cache Redis (Upstash)](#cache-redis-upstash)
10. [Frontend - Functionalitati](#frontend---functionalitati)
11. [Formate Date](#formate-date)
12. [Configurare si Deploy](#configurare-si-deploy)
13. [Variabile de Mediu](#variabile-de-mediu)
14. [Optimizari Performanta](#optimizari-performanta)

---

## Prezentare Generala

**Kuziini Preturi** este un instrument intern pentru echipa Kuziini care:

- **Compara preturile** produselor Samsung (TV-uri, soundbar-uri, frigidere, etc.) pe 4 magazine online din Romania
- **Colecteaza automat** preturile zilnic la ora 08:00 prin cron job
- **Stocheaza local** toate datele in Redis - aplicatia NU depinde de vendori in timpul zilei
- **Genereaza rapoarte** de miscare preturi (scumpiri, ieftiniri, istoric pe produs)
- **Exporta** date in Excel si PDF
- **Cos de cumparaturi** cu detectie automata a celui mai ieftin vendor si sugestii de pret

Vendorii monitorizati:
| Vendor | Website | Metoda |
|--------|---------|--------|
| **Samsung** | samsung.com/ro | API Shop Samsung + JSON-LD |
| **eMAG** | emag.ro | AJAX API + Suggest + curl |
| **Flanco** | flanco.ro | curl (TLS bypass) + DuckDuckGo |
| **Altex** | altex.ro | Cookie jar + Next.js API + curl |

---

## Arhitectura

```
                    +-------------------+
                    |   Vercel (Host)   |
                    |   Hobby Plan      |
                    +--------+----------+
                             |
              +--------------+--------------+
              |              |              |
     +--------v---+  +------v------+  +----v--------+
     | index.html |  | search.py   |  | cron.py     |
     | (Frontend) |  | (API)       |  | (Scraping)  |
     | PWA, Cart  |  | 12 endpoint |  | Zilnic 8:00 |
     +--------+---+  +------+------+  +------+------+
              |              |                |
              |       +------v------+         |
              |       | scraper.py  |         |
              |       | 2100+ linii |<--------+
              |       +------+------+
              |              |
              |    +---------+---------+
              |    |    |    |    |    |
              |   Sam  eMAG Fla  Alt  compari
              |   API  AJAX curl curl .ro
              |
              +-------->+---------------+
                        | Upstash Redis |
                        | (REST API)    |
                        | Cache + Hist  |
                        +---------------+
```

**Flux de date:**
1. **Cron (08:00 zilnic)** → scraper.py preia preturile → salveaza in Redis
2. **Utilizator** deschide app → frontend cere date din cache Redis
3. **Nicio interogare live** catre vendori in timpul zilei (independenta totala)
4. **Rapoarte** se genereaza din datele istorice stocate in Redis (90 zile)

---

## Stack Tehnologic

### Backend
| Tehnologie | Rol | Detalii |
|------------|-----|---------|
| **Python 3.12** | Runtime | Vercel Serverless Functions |
| **BeautifulSoup4** | HTML parsing | Extragere preturi din pagini web |
| **requests** | HTTP client | Samsung, eMAG (unde curl nu e necesar) |
| **lxml** | Parser HTML/XML | Performant, folosit cu BeautifulSoup |
| **openpyxl** | Excel parser | Lazy-loaded (doar daca products.json lipseste) |
| **curl** (subprocess) | TLS bypass | Flanco si Altex blocheaza Python SSL |
| **urllib** | Redis REST | Zero dependinte externe pentru cache |
| **ThreadPoolExecutor** | Paralel | Scraping multi-vendor (search live) |

### Frontend
| Tehnologie | Rol |
|------------|-----|
| **HTML5 / CSS3** | Layout responsive, Glassmorphism design |
| **Vanilla JavaScript** | Zero framework, ~900 linii |
| **localStorage** | Persistenta cos cumparaturi |
| **Intl.NumberFormat** | Formatare preturi RON |
| **PWA Manifest** | App instalabila pe mobil |

### Infrastructura
| Serviciu | Plan | Rol |
|----------|------|-----|
| **Vercel** | Hobby | Hosting, serverless functions, cron |
| **Upstash Redis** | Free | Cache distribuit (10K cmd/zi, 256MB) |
| **Git / GitHub** | - | Versionare si CI/CD automat |

---

## Platforme si Integrari

### 1. Vercel (Deployment & Runtime)
- **URL**: Deployed automat la fiecare push pe main
- **Serverless Functions**: Python cu `BaseHTTPRequestHandler`
- **Cron Jobs**: Trigger automat la schedule definit in `vercel.json`
- **Max Duration**: 60s per functie (Hobby plan)
- **Rewrites**: Toate cererile `/api/*` rutate la `search.py`, `/api/cron` la `cron.py`

### 2. Upstash Redis (Cache Distribuit)
- **Protocol**: REST API over HTTPS (nu necesita pip packages)
- **Autentificare**: Bearer token
- **Chei folosite**:
  - `price:{CODE}` - Preturi cache (TTL: 49h)
  - `cache:status` - Metadata cron (fara TTL)
  - `history:{CODE}` - Istoric preturi 90 zile (TTL: 91 zile)
  - `events:{YYYY-MM-DD}` - Evenimente cron (TTL: 7 zile)
  - `specs:{CODE}` - Specificatii Samsung (TTL: 7 zile)
- **Comenzi**: SET, GET, KEYS (via REST POST)

### 3. Samsung Shop API
- **URL**: `https://p1-smn3-api-cdn.shop.samsung.com/tokocommercewebservices/v2/ro`
- **Metoda**: GET direct pe endpoint produse
- **Returneaza**: Pret real de vanzare (nu PRP), imagine produs
- **Fallback**: JSON-LD de pe samsung.com/ro

### 4. eMAG
- **Metoda principala**: AJAX search-by-filters API (JSON)
- **Backup 1**: Suggest API (autocomplete)
- **Backup 2**: curl search cu redirect canonical
- **Backup 3**: Python requests
- **Validare**: Codul produsului trebuie sa apara pe pagina (previne potriviri gresite)

### 5. Flanco
- **Cerinta**: Warmup pe homepage (cookie TLS obligatoriu)
- **Metoda**: curl cu TLS bypass (Python SSL blocata de server)
- **Fallback**: DuckDuckGo `site:flanco.ro` search
- **Pret**: CSS selectors (finalPrice, special-price) + JSON-LD

### 6. Altex
- **Cerinta**: Cookie jar persistent (multi-step)
- **Metoda**: curl cu `-b`/`-c` flags (salvare/reutilizare cookies)
- **Detectie**: Next.js `__NEXT_DATA__` + buildId API routes
- **Fallback**: DuckDuckGo search + HTML slug extraction
- **Pret**: Special price prioritar fata de regular price

### 7. Compari.ro / PriceGuru.ro (Fallback)
- **Folosit cand**: Flanco sau Altex sunt complet blocate
- **Metoda**: Scraping pagini comparator preturi
- **Extragere**: Nume vendor + pret din lista retaileri

### 8. DuckDuckGo (Search Fallback)
- **Folosit pentru**: Gasirea URL-urilor produselor cand search-ul direct pe vendor esueaza
- **Query**: `site:flanco.ro "QE55QN90F"` sau `site:altex.ro "QE55QN90F"`

---

## Structura Proiect

```
kuziini-preturi/
|-- api/
|   |-- __init__.py          # Vercel init
|   |-- search.py            # Handler principal (12 endpoints)
|   |-- cron.py              # Cron job scraping zilnic
|   |-- scraper.py           # Logica scraping (2100+ linii)
|   |-- cache.py             # Wrapper Upstash Redis REST
|
|-- public/
|   |-- index.html           # Frontend complet (~3000 linii)
|   |-- manifest.json        # PWA manifest
|   |-- icon-192.png         # App icon (192x192)
|   |-- icon-512.png         # App icon (512x512)
|
|-- data/
|   |-- products.json        # 86 produse Samsung (pre-parsed din Excel)
|
|-- vercel.json              # Config deploy + cron schedule
|-- requirements.txt         # Dependinte Python
```

---

## Endpoints API

Toate endpoint-urile sunt servite de `search.py` (cu exceptia `/api/cron`).

### Endpoints Principale

| Endpoint | Parametri | Descriere |
|----------|-----------|-----------|
| `GET /api/search` | `code` (obligatoriu), `vendor` (optional) | Cauta pret produs. Returneaza din cache sau info Excel |
| `GET /api/products` | - | Lista completa produse cu cod, categorie, pret, diagonala |
| `GET /api/specs` | `code` | Specificatii tehnice Samsung (scrape sau cache 7 zile) |
| `GET /api/reports` | `code` (optional) | Fara code: sumar toate produsele. Cu code: istoric per produs |
| `GET /api/events` | `date` (optional, YYYY-MM-DD) | Evenimente cron (erori vendori, timeout-uri) |
| `GET /api/cache_status` | - | Stare cache (total, ultimul update, batch index) |

### Endpoints Utilitare

| Endpoint | Descriere |
|----------|-----------|
| `GET /api/version` | Versiune app + stare cache |
| `GET /api/ping` | Health check |
| `GET /api/test_redis` | Test conexiune Redis (SET + GET) |
| `GET /api/test_excel` | Test incarcare produse + timp |
| `GET /api/reload_excel` | Forteaza reincarcare products.json |

### Endpoint Cron

| Endpoint | Parametri | Descriere |
|----------|-----------|-----------|
| `GET /api/cron` | `chain=1`, `reset=1` | Procesare batch. `reset` reincepe de la 0 |

---

## Sistem Cron - Scraping Automat

### Schedule
- **Ora**: 08:00 Romania (05:00 UTC)
- **Frecventa**: Zilnic
- **Config**: `vercel.json` → `"schedule": "0 5 * * *"`

### Flux de executie

```
[Vercel Trigger 05:00 UTC]
        |
        v
  Incarca products.json (86 produse)
        |
        v
  Citeste batch_index din Redis
        |
        v
  Proceseaza 1 produs (BATCH_SIZE=1)
  |-- search_product(code, cron_mode=True)
  |   |-- Samsung API
  |   |-- eMAG AJAX/curl
  |   |-- Flanco curl
  |   |-- Altex curl
  |
  |-- Salveaza in Redis (price:{code}, TTL 49h)
  |-- Salveaza istoric (history:{code}, 90 zile)
  |
  |-- Vendori lipsa? (emag, altex null)
  |   |-- Retry individual cu search_single_vendor()
  |   |-- Daca reuseste: re-save cache + istoric
  |   |-- Daca esueaza: save_cron_event() (vendor_error/unavailable/timeout)
  |
  |-- Actualizeaza cache:status (batch_index++)
  |
  v
  Mai sunt produse? --Da--> trigger_next_batch(host)
        |                         |
       Nu                   GET /api/cron?chain=1
        |                   (fire-and-forget)
        v
  batch_index = 0 (reset)
  status = COMPLETED
```

### Timpi si limite
- **Budget per invocatie**: 60s (Vercel Hobby)
- **Safety margin**: 50s (opreste procesarea dupa 50s)
- **Retry vendors**: Doar daca elapsed < 45s
- **Scraping per produs**: ~30-50s (4 vendori)
- **Total 86 produse**: ~86 invocatii chain (1-2 ore)

### Tracking evenimente
Cand un vendor esueaza, se salveaza in Redis:
```json
{
  "time": "08:15",
  "code": "QE55QN90FATXXH",
  "type": "vendor_unavailable",
  "details": "emag: produs negasit sau pret indisponibil"
}
```

Tipuri de evenimente:
- `vendor_unavailable` - Produsul nu exista pe vendor
- `vendor_error` - Eroare retea/parsare
- `timeout` - Depasire timp alocat

---

## Vendori - Metode Scraping

### Variante Cod Produs
Fiecare vendor primeste mai multe variante de cautare:
```
QE55QN90FATXXH  →  [QE55QN90FATXXH, QE55QN90FAT, QE55QN90F, QE55QN90]
```
Se inlatura progresiv: sufixul regional (TXXH, AUXXH), litera varianta (A-H).

### Prioritate Extragere Pret

| Vendor | Ordine incercare |
|--------|-----------------|
| **Samsung** | API Shop → JSON-LD → text pagina |
| **eMAG** | JSON-LD → meta tags → itemprop → CSS → __NEXT_DATA__ → GTM dataLayer → primul pret |
| **Flanco** | CSS (finalPrice, special-price) → JSON-LD → text |
| **Altex** | __NEXT_DATA__ → JSON-LD → meta → itemprop → CSS → GTM → pret minim |

### Formatare Preturi Romania
```
"4.799,99 lei" → 4799.99  (float)
"21.999"       → 21999.0
"799,99"       → 799.99
```

---

## Cache Redis (Upstash)

### Structura chei

| Cheie | Continut | TTL |
|-------|----------|-----|
| `price:{CODE}` | `{prices, urls, image_url, category, kuziini_price, cached_at}` | 49h (176400s) |
| `cache:status` | `{total_cached, total_products, last_update, batch_index}` | Fara expirare |
| `history:{CODE}` | `{"2026-03-10": {samsung: 4499, emag: 4599, ...}, ...}` | 91 zile (7862400s) |
| `events:{DATE}` | `[{time, code, type, details}, ...]` | 7 zile (604800s) |
| `specs:{CODE}` | `{sections: [{name, items: [{title, value}]}]}` | 7 zile (604800s) |

### Model cache-first
1. Utilizator cere pret → se cauta in Redis (`price:{CODE}`)
2. Daca exista si `cached_at` < 48h → returneaza din cache
3. Daca nu exista → returneaza info din Excel + mesaj "preturile se actualizeaza automat"
4. **NU se face scraping live** la cererea utilizatorului

### Comunicare Redis
```python
# POST la REDIS_URL cu Bearer token
data = json.dumps(["SET", "price:QE55QN90F", payload, "EX", 176400])
req = urllib.request.Request(REDIS_URL, data=data, method='POST')
req.add_header('Authorization', f'Bearer {REDIS_TOKEN}')
```

---

## Frontend - Functionalitati

### 1. Cautare Produs
- Input cod Samsung (auto-uppercase, trim)
- Afisare: pret Kuziini + 4 vendori cu link-uri directe
- Imagine produs (de la Samsung sau vendori)
- Badge "Cached" cu varsta cache in minute
- Badge "Indisponibil" pentru vendori fara pret

### 2. Filtre Produse
- **Grup**: TV, Audio, Frigider, Masina de spalat, etc.
- **Categorie**: NeoQLED, QLED, Crystal UHD, Soundbar, etc.
- **Diagonala**: 43", 50", 55", 65", 75", 85" (extras din cod cu regex `QE(\d{2})`)
- Dropdown dinamic cu numar produse filtrate

### 3. Cos de Cumparaturi
- Persistenta `localStorage` (supravietuieste inchiderea browser-ului)
- Detectie automata cel mai ieftin vendor
- **Alerta** daca pretul Kuziini > cel mai ieftin vendor
- Sugestie pret: cel_mai_ieftin * 1.05 (adaos 5%)
- Control cantitate (+/-)
- **Export Excel** (.xls) cu toate detaliile
- Total cos afisat in header

### 4. Rapoarte Preturi
- **Tab Sumar**: Toate produsele cu preturi actuale + variatii
- **Tab Scumpiri**: Produse cu cel putin o crestere de pret (sortate desc)
- **Tab Ieftiniri**: Produse cu cel putin o scadere de pret (sortate asc)
- **Tab Evenimente**: Erori cron/vendori din ziua curenta
- Click pe produs → grafic istoric 30 zile (bare pe vendor + preturi inline)
- **Export Excel** per tab
- **Print PDF** per tab

### 5. Specificatii Tehnice Samsung
- Buton "Specificatii" la fiecare produs cautat
- Sectiuni: Ecran, Audio, Conectivitate, Smart TV, etc.
- Accordion colapsabil (prima sectiune deschisa)
- Cache 7 zile in Redis

### 6. PWA (Progressive Web App)
- `manifest.json` cu name, icons, theme_color
- Instalabila pe Android/iOS (Add to Home Screen)
- Display: standalone (fara bara browser)
- Theme: #7c3aed (violet)

### 7. Design
- **Glassmorphism**: Fundal gradient + card-uri semi-transparente
- **Responsive**: Mobile-first, breakpoints la 768px si 480px
- **Culori vendori**: Samsung albastru (#1428a0), eMAG galben (#f5a623), Flanco rosu (#e11d48), Altex violet (#7c3aed)
- **Animatii**: Loading skeleton, fade-in card-uri

---

## Formate Date

### Produs (products.json)
```json
{
  "QE55QN90FATXXH": {
    "category": "NeoQLED",
    "price": 4803.78,
    "group": "TV"
  }
}
```

### Raspuns Search API
```json
{
  "code": "QE55QN90FATXXH",
  "category": "NeoQLED",
  "kuziini_price": 4803.78,
  "image_url": "https://images.samsung.com/...",
  "prices": {
    "samsung": 4499.99,
    "emag": 4599.00,
    "flanco": null,
    "altex": 4549.99
  },
  "urls": {
    "samsung": "https://www.samsung.com/ro/tvs/...",
    "emag": "https://www.emag.ro/...",
    "altex": "https://altex.ro/..."
  },
  "cached": true,
  "cache_age_min": 120
}
```

### Istoric Preturi
```json
{
  "2026-03-09": {"samsung": 4499.99, "emag": 4599.00, "flanco": null, "altex": 4549.99},
  "2026-03-10": {"samsung": 4399.99, "emag": 4599.00, "flanco": 4699.00, "altex": 4549.99}
}
```

### Eveniment Cron
```json
{
  "time": "05:23",
  "code": "QE55QN90FATXXH",
  "type": "vendor_unavailable",
  "details": "emag: produs negasit sau pret indisponibil"
}
```

---

## Configurare si Deploy

### Prerequisites
- Cont Vercel (Hobby plan e suficient)
- Cont Upstash Redis (free tier)
- Repository Git conectat la Vercel

### Pasi Deploy

1. **Clone repository**
2. **Seteaza variabilele de mediu** in Vercel Dashboard (vezi sectiunea urmatoare)
3. **Push pe main** → Vercel face deploy automat
4. **Trigger cron manual** (prima data): `GET /api/cron?reset=1`
5. **Verifica**: `GET /api/cache_status` → trebuie sa arate progresul

### Actualizare Produse
1. Modifica `data/products.json` (sau incarca Excel nou)
2. Push pe main
3. Dupa deploy: `GET /api/cron?reset=1` (reporneste scanarea de la 0)

---

## Variabile de Mediu

Setate in **Vercel Dashboard → Settings → Environment Variables**:

| Variabila | Obligatorie | Descriere |
|-----------|-------------|-----------|
| `UPSTASH_REDIS_REST_URL` | Da | URL-ul Redis (ex: `https://xyz.upstash.io`) |
| `UPSTASH_REDIS_REST_TOKEN` | Da | Token Bearer pentru autentificare Redis |

Fara aceste variabile, aplicatia functioneaza dar:
- Nu are cache (fiecare cerere ar necesita scraping live)
- Nu salveaza istoric preturi
- Nu genereaza rapoarte
- Nu salveaza evenimente cron

---

## Optimizari Performanta

| Optimizare | Descriere |
|------------|-----------|
| **Cache-first** | Nicio interogare live catre vendori in timpul zilei |
| **Lazy-load openpyxl** | Se incarca doar daca products.json lipseste |
| **curl TLS bypass** | Flanco/Altex blocheaza Python SSL, curl trece |
| **Session warmup** | O vizita pe Google la cold start (cookies realiste) |
| **Batch processing** | 1 produs/invocatie cron (previne timeout 60s) |
| **Self-chaining** | Cron se auto-apeleaza pentru urmatorul batch |
| **49h TTL** | Preturile persista intre 2 cron runs consecutive |
| **90 zile istoric** | Suficient pentru analiza trend-uri sezoniere |
| **500 events/zi cap** | Previne explozia de memorie la erori masive |
| **Fire-and-forget** | Chain request cu timeout 5s, nu asteapta raspuns |

---

## Dezvoltat de

**Digital Store / Kuziini** - Instrument intern pentru monitorizarea preturilor pe piata Samsung Romania.

Aplicatia este 100% self-hosted pe Vercel cu zero dependinte de API-uri platite.
Toate datele sunt stocate in Redis (Upstash free tier) si sunt independente de disponibilitatea vendorilor.

---

## Platforme Legate

| Platforma | Rol | Legatura |
|-----------|-----|----------|
| **GitHub** | Versionare cod si CI/CD | Repository-ul sursa; fiecare push pe `main` declanseaza deploy automat pe Vercel |
| **Vercel** | Hosting si runtime | Serveste frontend-ul, ruleaza serverless functions (Python) si cron jobs; conectat direct la GitHub |
| **Upstash** | Baza de date Redis (cloud) | Stocheaza cache-ul preturilor, istoricul si evenimentele; conectata la Vercel prin variabile de mediu (`UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`) |

**Flux intre platforme:**
```
GitHub (cod sursa)  -->  Vercel (deploy automat la push)  <-->  Upstash Redis (date si cache)
```

- **GitHub → Vercel**: Integrare Git nativa. Vercel monitorizeaza branch-ul `main` si face deploy automat la fiecare commit.
- **Vercel → Upstash**: Serverless functions comunica cu Redis prin REST API securizat (Bearer token). Variabilele de mediu sunt configurate in Vercel Dashboard.
- **Upstash → Vercel**: Redis raspunde la cererile API din functiile serverless (cache preturi, istoric, evenimente cron).
