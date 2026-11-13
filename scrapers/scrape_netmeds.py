"""
Netmeds scraper.

Netmeds runs on the Fynd commerce platform (not a custom build) — confirmed
by opening the site in a real browser and watching network traffic. The one
endpoint we need gives everything in a SINGLE call, no per-product follow-up
required (unlike PharmEasy, which needs a second request per product):

    GET https://www.netmeds.com/ext/search/application/api/v1.0/products
        ?page_id=*&page_size=12&q=<query>

Verified independently with plain `curl` (no login/cookies needed).

Each item in the response's "items" array has, at the TOP level (not buried
in attributes): name, slug, price.effective/marked, discount, sellable
(stock), tags (contains "rx" or "non-rx"), brand.

NOTE: this endpoint returns platform-wide pricing, not guaranteed
pincode-local pricing/stock — Netmeds' pincode-specific seller/delivery data
lives in a separate per-product endpoint
(catalog/v4.0/products/<slug>/sizes/<size>/sellers) found during the same
browser session. For MVP purposes, the search endpoint's price is used as a
reasonable default; swap in the per-product endpoint if pincode-exact
pricing turns out to matter for the comparison.
"""

import time
import re
from dataclasses import dataclass
from typing import Optional
import requests

PLATFORM_CODE = "netmeds"
REQUEST_DELAY_SECONDS = 3.0
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

SEARCH_API = "https://www.netmeds.com/ext/search/application/api/v1.0/products"
PRODUCT_URL_TEMPLATE = "https://www.netmeds.com/products/{slug}"


@dataclass
class RawListing:
    platform_sku_id: str
    raw_title: str
    product_url: str
    pack_label: Optional[str]   # parsed out of the title, e.g. "15's" — Netmeds doesn't give this structured
    mrp: Optional[float]
    selling_price: Optional[float]
    in_stock: bool
    rx_required: bool


def _to_float(value) -> Optional[float]:
    """Defensive cast: Netmeds' Fynd-platform JSON doesn't guarantee numeric
    fields come through as numbers rather than numeric strings. A stray
    string here breaks cross-platform price comparisons downstream (can't
    compare str to float), so never trust the raw JSON type."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def search(query: str, page_size: int = 12) -> list[RawListing]:
    headers = {"User-Agent": USER_AGENT}
    params = {"page_id": "*", "page_size": page_size, "q": query}

    resp = requests.get(SEARCH_API, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)

    payload = resp.json()
    items = payload.get("items", []) or []

    listings = []
    for it in items:
        if it.get("type") != "product":
            continue

        price = it.get("price", {}) or {}
        effective = _to_float((price.get("effective") or {}).get("min"))
        marked = _to_float((price.get("marked") or {}).get("min"))

        tags = it.get("tags", []) or []
        rx_required = "rx" in tags and "non-rx" not in tags

        slug = it.get("slug", "")
        listings.append(RawListing(
            platform_sku_id=str(it.get("uid") or it.get("item_code") or slug),
            raw_title=it.get("name", ""),
            product_url=PRODUCT_URL_TEMPLATE.format(slug=slug),
            pack_label=_extract_pack_label(it.get("name", "")),
            mrp=marked,
            selling_price=effective or marked,
            in_stock=bool(it.get("sellable", False)),
            rx_required=rx_required,
        ))

    return listings


def _extract_pack_label(name: str) -> Optional[str]:
    # Netmeds titles end in a pack count like "15's" or "15'S" — pull it out
    # as a crude pack_label for normalize/match.py to parse further.
    m = re.search(r"(\d+)\s*'?[sS]\b", name)
    return m.group(0) if m else None


if __name__ == "__main__":
    import sys
    query = sys.argv[1] if len(sys.argv) > 1 else "dolo 650 tablet"
    results = search(query)
    print(f"Found {len(results)} listings for {query!r}:\n")
    for r in results:
        print(f"  [{r.platform_sku_id}] {r.raw_title}")
        print(f"      MRP ₹{r.mrp}  →  Selling ₹{r.selling_price}  |  "
              f"Rx: {r.rx_required}  |  Stock: {r.in_stock}")
        print(f"      {r.product_url}")
