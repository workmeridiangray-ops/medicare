"""MedCompare API with isolated provider failures and conservative matching."""
import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from scrapers import scrape_1mg, scrape_pharmeasy, scrape_netmeds
from normalize.match import parse_listing_title, match_score, similarity, _strength_key

app = FastAPI(title='MedCompare')
logger = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / 'static'
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 180
_CACHE_MAX_ENTRIES = 256
_CACHE_LOCK = Lock()

def full_text(r) -> str:
    title = r.raw_title
    pack = getattr(r, 'pack_label', '') or ''
    # Netmeds extracts pack_label from its title: don't append it twice.
    return f'{title} {pack}'.strip() if pack.lower() not in title.lower() else title

def _price(r) -> Optional[float]:
    try:
        value = float(r.selling_price)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None

def _relevance(query: str, text: str) -> tuple[int, float]:
    # Plain character-similarity (SequenceMatcher) rates a short query against
    # a short-but-wrong title (e.g. "Dolo" vs "Dologel", 7 chars) higher than
    # against the right one (e.g. "Dolo 650 Tablet", 16 chars) purely because
    # it's shorter overall - a string-length artifact, not a real match.
    # Verified live: searching bare "Dolo" picked "Dologel" as the reference.
    # Primary key instead: how many whole query words appear as whole words
    # in the title ("dolo" must be its own word, not a substring of
    # "dologel"), so a real token match always outranks a shorter near-miss.
    # Similarity stays as the tiebreaker among equally-relevant titles.
    query_words = re.findall(r'[a-z0-9]+', query.lower())
    title_words = set(re.findall(r'[a-z0-9]+', text.lower()))
    overlap = sum(1 for w in query_words if w in title_words)
    return (overlap, similarity(query, text))

def run_compare(query: str) -> dict:
    sources = {
        '1mg': lambda: scrape_1mg.search(query),
        'PharmEasy': lambda: scrape_pharmeasy.search(query, max_products=5),
        'Netmeds': lambda: scrape_netmeds.search(query),
    }
    listings, errors = {}, {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {name: pool.submit(search) for name, search in sources.items()}
        for name, future in futures.items():
            try:
                listings[name] = future.result()
            except Exception:
                logger.exception('Provider search failed: %s', name)
                listings[name] = []
                errors[name] = 'Provider temporarily unavailable; prices could not be checked.'
    # Fall back to another available provider rather than requiring 1mg.
    candidates = next((rows for rows in listings.values() if rows), [])
    reference_result = max(candidates, key=lambda r: _relevance(query, full_text(r)), default=None)
    reference = parse_listing_title(full_text(reference_result)) if reference_result else None
    if reference and match_score(reference, reference) < .80:
        # A title missing its unit/pack cannot anchor a confirmed comparison.
        # Prefer a complete listing of the same brand, keeping the selected
        # reference visible rather than silently inferring missing units.
        complete = [r for rows in listings.values() for r in rows
                    if (parsed := parse_listing_title(full_text(r))).brand_or_molecule == reference.brand_or_molecule
                    and match_score(parsed, parsed) >= .80
                    and reference.strength
                    and len(_strength_key(reference.strength) or []) == len(_strength_key(parsed.strength) or [])
                    and all(lv == rv and (lu is None or lu == ru)
                            for (lv, lu), (rv, ru) in zip(_strength_key(reference.strength) or [],
                                                        _strength_key(parsed.strength) or []))]
        if complete:
            reference_result = max(complete, key=lambda r: _relevance(query, full_text(r)))
            reference = parse_listing_title(full_text(reference_result))
    platforms, available = {}, []
    for name, rows in listings.items():
        matched, unmatched = [], []
        for r in rows:
            score = match_score(reference, parse_listing_title(full_text(r)))
            price = _price(r)
            entry = {'title': r.raw_title, 'pack_label': getattr(r, 'pack_label', None),
                     'product_url': getattr(r, 'product_url', None), 'price': price,
                     'in_stock': bool(getattr(r, 'in_stock', False)),
                     'rx_required': bool(getattr(r, 'rx_required', False)), 'score': score}
            (matched if score >= .80 else unmatched).append(entry)
            if score >= .80 and price is not None and entry['in_stock']:
                available.append({'platform': name, 'title': r.raw_title, 'price': price})
        platforms[name] = {'matched': matched, 'unmatched': unmatched,
                           'error': errors.get(name),
                           'status': 'error' if name in errors else ('ok' if rows else 'empty')}
    return {'query': query, 'reference': reference_result.raw_title if reference_result else None,
            'platforms': platforms, 'cheapest': min(available, key=lambda r: r['price'], default=None),
            'checked_at': time.time(),
            'pricing_note': 'Indicative listed prices; location, delivery fees and checkout discounts are not included.'}

@app.get('/api/compare')
def compare(q: str = Query(..., min_length=2, max_length=120)):
    query = ' '.join(q.split())
    if len(query) < 2:
        raise HTTPException(status_code=422, detail='Enter at least two non-space characters.')
    key, now = query.lower(), time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
    result = run_compare(query)
    # Do not cache partial outages: a retry should be allowed to recover.
    if not any(b['error'] for b in result['platforms'].values()):
        with _CACHE_LOCK:
            expired = [k for k, (stamp, _) in _CACHE.items() if now - stamp >= _CACHE_TTL_SECONDS]
            for k in expired:
                del _CACHE[k]
            if len(_CACHE) >= _CACHE_MAX_ENTRIES:
                del _CACHE[min(_CACHE, key=lambda k: _CACHE[k][0])]
            _CACHE[key] = (time.monotonic(), result)
    return result

@app.get('/health')
def health():
    return {'ok': True}

app.mount('/static', StaticFiles(directory=STATIC), name='static')

@app.get('/')
def index():
    return FileResponse(STATIC / 'index.html')
