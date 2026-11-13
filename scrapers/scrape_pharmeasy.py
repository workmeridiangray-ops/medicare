"""
PharmEasy scraper.

Two real endpoints found by opening the site in a real browser and watching
network traffic, then re-verified with plain `curl` (no login needed):

1. Search/autocomplete — gives candidate products with slugs, but NO price:
     GET https://pharmeasy.in/api/search/searchTypeAhead?q=<query>

2. Product detail page — fully server-rendered (Next.js), price/stock/
   molecule/strength all sit in a <script id="__NEXT_DATA__"> JSON blob in
   the plain HTML response. No JS execution needed to read it:
     GET https://pharmeasy.in/online-medicine-order/<slug>

   productDetails fields used here: name, molecule, drugStrengthValue,
   drugStrengthUnit, measurementUnit, manufacturer, isRxRequired,
   isAvailable, costPrice (MRP), salePrice, discountPercent.

Because PharmEasy's structured data already gives molecule + strength
directly (no title-regex guessing needed, unlike 1mg), listings from this
scraper should be matched on (molecule, drugStrengthValue+unit, pack size)
rather than fuzzy brand-name parsing where possible — more reliable than
normalize/match.py's text-based approach. See NOTE in scrape() below.

Cost of this approach: 1 search call + 1 PDP call PER product, vs. 1 call
for a whole results page on 1mg. Slower, so rate-limit accordingly.
"""

import time
import re
import json
from dataclasses import dataclass
from typing import Optional
import requests

PLATFORM_CODE = "pharmeasy"
REQUEST_DELAY_SECONDS = 3.0
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

SEARCH_API = "https://pharmeasy.in/api/search/searchTypeAhead"
PDP_URL_TEMPLATE = "https://pharmeasy.in/online-medicine-order/{slug}"

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


@dataclass
class RawListing:
    platform_sku_id: str
    raw_title: str
    product_url: str
    molecule: Optional[str]       # structured, not regex-guessed
    strength_value: Optional[str]
    strength_unit: Optional[str]
    pack_label: Optional[str]     # e.g. "15 Tablet(s) in Strip"
    manufacturer: Optional[str]
    mrp: Optional[float]
    selling_price: Optional[float]
    in_stock: bool
    rx_required: bool


def _find_candidate_slugs(query: str, limit: int = 8) -> list[tuple[str, str]]:
    """Returns [(product_id, slug), ...] for productType==1 (actual medicine
    SKUs, not brand headers) from the typeahead endpoint."""
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(SEARCH_API, params={"q": query}, headers=headers, timeout=15)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)

    payload = resp.json()
    products = payload.get("data", {}).get("products", []) or []

    candidates = []
    for p in products:
        if p.get("productType") == 1 and p.get("slug"):
            candidates.append((str(p.get("productId")), p["slug"]))
        if len(candidates) >= limit:
            break
    return candidates


def _to_float(value) -> Optional[float]:
    """PharmEasy's embedded __NEXT_DATA__ JSON doesn't guarantee costPrice/
    salePrice come through as numbers — on at least one live product they
    arrived as numeric strings (e.g. "32.28"), which crashed downstream code
    comparing prices across platforms (`min(..., key=lambda x: x[2])` can't
    compare a str to a float). Cast defensively rather than trust the type."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_pdp(slug: str) -> Optional[RawListing]:
    url = PDP_URL_TEMPLATE.format(slug=slug)
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)

    m = _NEXT_DATA_RE.search(resp.text)
    if not m:
        return None

    data = json.loads(m.group(1))
    pd = data.get("props", {}).get("pageProps", {}).get("productDetails")
    if not pd:
        return None

    return RawListing(
        platform_sku_id=str(pd.get("productId")),
        raw_title=pd.get("name", ""),
        product_url=url,
        molecule=pd.get("molecule"),
        strength_value=pd.get("drugStrengthValue"),
        strength_unit=pd.get("drugStrengthUnit"),
        pack_label=pd.get("measurementUnit"),
        manufacturer=pd.get("manufacturer"),
        mrp=_to_float(pd.get("costPrice")),
        selling_price=_to_float(pd.get("salePrice")) or _to_float(pd.get("costPrice")),
        in_stock=bool(pd.get("isAvailable", False)),
        rx_required=bool(pd.get("isRxRequired", False)),
    )


def search(query: str, max_products: int = 5) -> list[RawListing]:
    """NOTE: this makes 1 + max_products HTTP requests (search + one PDP
    fetch per candidate), each with its own rate-limit delay. For a query
    that returns many candidates, consider lowering max_products — this is
    meant for targeted lookups (e.g. matching one canonical item), not bulk
    catalog crawling."""
    candidates = _find_candidate_slugs(query, limit=max_products)
    listings = []
    for _, slug in candidates:
        listing = _fetch_pdp(slug)
        if listing:
            listings.append(listing)
    return listings


if __name__ == "__main__":
    import sys
    query = sys.argv[1] if len(sys.argv) > 1 else "dolo 650"
    results = search(query, max_products=5)
    print(f"Found {len(results)} listings for {query!r}:\n")
    for r in results:
        print(f"  [{r.platform_sku_id}] {r.raw_title} ({r.pack_label})")
        print(f"      Molecule: {r.molecule}  Strength: {r.strength_value}{r.strength_unit}")
        print(f"      MRP ₹{r.mrp}  →  Selling ₹{r.selling_price}  |  "
              f"Rx: {r.rx_required}  |  Stock: {r.in_stock}  |  Mfr: {r.manufacturer}")
        print(f"      {r.product_url}")
