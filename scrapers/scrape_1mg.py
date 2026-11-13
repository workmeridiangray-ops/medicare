"""
1mg scraper — hits their real internal search API directly.

How this endpoint was found: the 1mg search page is a client-rendered React
app with no product data in the server HTML. Opening the page in a real
browser and inspecting network requests showed it calls:

    GET https://www.1mg.com/pwa-dweb-api/api/v4/search/all
        ?q=<query>&city=<city>&page_number=0&per_page=10
        &types=sku,allopathy&sort=relevance&fetch_eta=true
        &is_city_serviceable=true

Verified independently with a plain `curl` (no login, no cookies, just a
browser User-Agent header) — confirmed 200 OK with full product/price JSON.
This is an UNDOCUMENTED internal API, not a published public API: it can
change or get rate-limited/blocked without notice, and calling it at scale is
a materially different (and more clearly ToS-sensitive) thing than a human
browsing the page. Treat this as a fragile integration:
  - rate-limit hard (see REQUEST_DELAY_SECONDS)
  - identify your bot honestly if you add a custom header for it
  - expect to re-discover the endpoint shape periodically if it breaks
  - re-read README.md's legal/ToS note before running this against
    anything beyond a handful of manual test queries
"""

import time
import re
from dataclasses import dataclass
from typing import Optional
import requests

PLATFORM_CODE = "1mg"
REQUEST_DELAY_SECONDS = 3.0
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

SEARCH_API = "https://www.1mg.com/pwa-dweb-api/api/v4/search/all"

# 1mg's search API takes a city name, not a pincode, for this endpoint.
# Map the pincodes you care about to the city name 1mg expects.
PINCODE_TO_CITY = {
    "411045": "Pune",
    "122019": "Gurgaon",
    # add more as needed
}


@dataclass
class RawListing:
    platform_sku_id: str
    raw_title: str
    product_url: str
    pack_label: Optional[str]   # e.g. "strip of 15 tablets" — feeds normalize/match.py's pack_size parsing
    mrp: Optional[float]
    selling_price: Optional[float]
    in_stock: bool
    rx_required: bool
    eta_label: Optional[str]


def _parse_rupees(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"[\d,]+\.?\d*", text.replace("₹", ""))
    return float(m.group(0).replace(",", "")) if m else None


def search(query: str, pincode: str = "411045", per_page: int = 20) -> list[RawListing]:
    city = PINCODE_TO_CITY.get(pincode, "Pune")
    params = {
        "q": query,
        "city": city,
        "filter": "",
        "page_number": 0,
        "scroll_id": "",
        "per_page": per_page,
        "types": "sku,allopathy",
        "sort": "relevance",
        "fetch_eta": "true",
        "is_city_serviceable": "true",
    }
    headers = {"User-Agent": USER_AGENT}

    resp = requests.get(SEARCH_API, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)  # pay the delay even on success — don't hammer them

    payload = resp.json()
    raw_results = payload.get("data", {}).get("search_results", [])

    listings = []
    for r in raw_results:
        if r.get("type") not in ("drug", "otc"):
            continue  # skip ads, banners, non-product entries mixed into results

        prices = r.get("prices", {}) or {}
        mrp = _parse_rupees(prices.get("mrp"))
        selling = _parse_rupees(prices.get("discounted_price")) or mrp

        listings.append(RawListing(
            platform_sku_id=str(r.get("id")),
            raw_title=r.get("name", ""),
            product_url="https://www.1mg.com" + r.get("url", ""),
            pack_label=r.get("label"),
            mrp=mrp,
            selling_price=selling,
            in_stock=bool(r.get("available", False)),
            rx_required=bool(r.get("rx_required", False)),
            eta_label=_strip_html(r.get("eta")),
        ))

    return listings


def _strip_html(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return re.sub(r"<[^>]+>", "", text).strip()


if __name__ == "__main__":
    import sys
    import json

    query = sys.argv[1] if len(sys.argv) > 1 else "dolo 650"
    results = search(query)
    print(f"Found {len(results)} listings for {query!r}:\n")
    for r in results:
        print(f"  [{r.platform_sku_id}] {r.raw_title} ({r.pack_label})")
        print(f"      MRP ₹{r.mrp}  →  Selling ₹{r.selling_price}  |  "
              f"Rx: {r.rx_required}  |  Stock: {r.in_stock}  |  ETA: {r.eta_label}")
        print(f"      {r.product_url}")
