"""
MedCompare web app — FastAPI backend serving a /api/compare endpoint that
runs the same scrapers/matching logic verified in the CLI version, plus a
static single-page frontend.

This exists because the pharmacy sites' own APIs don't send a CORS header
that would let a browser call them directly (checked by hand — see
PLATFORM_STATUS.md), so a real server has to sit in between.
"""

import time
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from scrapers import scrape_1mg, scrape_pharmeasy, scrape_netmeds
from normalize.match import parse_listing_title, match_score, similarity

app = FastAPI(title="MedCompare")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Tiny in-memory cache so repeated searches for the same thing within a few
# minutes don't re-hit the live sites every time a family member refreshes.
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 180


def full_text(r) -> str:
    return f"{r.raw_title} {getattr(r, 'pack_label', '') or ''}".strip()


def _price(r) -> Optional[float]:
    try:
        return float(r.selling_price) if r.selling_price is not None else None
    except (TypeError, ValueError):
        return None


def run_compare(query: str) -> dict:
    onemg_results = scrape_1mg.search(query)
    pharmeasy_results = scrape_pharmeasy.search(query, max_products=5)
    netmeds_results = scrape_netmeds.search(query)

    if not onemg_results:
        return {"query": query, "reference": None, "platforms": {}, "cheapest": None}

    # Same fix as the CLI tool: pick the 1mg result that's textually closest
    # to the query itself, not just the top search-result slot, since 1mg's
    # own ranking can return a loosely related product first.
    best_idx, best_sim = 0, -1.0
    for i, r in enumerate(onemg_results):
        sim = similarity(query, full_text(r))
        if sim > best_sim:
            best_idx, best_sim = i, sim
    reference_result = onemg_results[best_idx]
    reference = parse_listing_title(full_text(reference_result))

    platforms = {}
    all_matched = []
    for platform, results in [
        ("1mg", onemg_results),
        ("PharmEasy", pharmeasy_results),
        ("Netmeds", netmeds_results),
    ]:
        matched, unmatched = [], []
        for r in results:
            parsed = parse_listing_title(full_text(r))
            score = match_score(reference, parsed)
            price = _price(r)
            entry = {
                "title": r.raw_title,
                "price": price,
                "in_stock": bool(getattr(r, "in_stock", True)),
                "rx_required": bool(getattr(r, "rx_required", False)),
                "score": round(score, 2),
            }
            if score >= 0.80:
                matched.append(entry)
                if price is not None:
                    all_matched.append((platform, r.raw_title, price))
            else:
                unmatched.append(entry)
        platforms[platform] = {"matched": matched, "unmatched": unmatched}

    cheapest = None
    if all_matched:
        plat, title, price = min(all_matched, key=lambda x: x[2])
        cheapest = {"platform": plat, "title": title, "price": price}

    return {
        "query": query,
        "reference": reference_result.raw_title,
        "platforms": platforms,
        "cheapest": cheapest,
    }


@app.get("/api/compare")
def compare(q: str = Query(..., min_length=2)):
    key = q.strip().lower()
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    result = run_compare(q.strip())
    _CACHE[key] = (now, result)
    return result


@app.get("/health")
def health():
    return {"ok": True}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
