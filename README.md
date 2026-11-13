# MedCompare

A personal medicine price-comparison tool across 1mg, PharmEasy, and
Netmeds. Search any medicine and see live prices side by side, with the
cheapest confirmed match highlighted.

Built for personal/family use — not a commercial product. Apollo 24/7 and
Truemeds aren't included; see `PLATFORM_STATUS.md` for why.

## Running locally

```
pip install -r requirements.txt
uvicorn app:app --reload
```

Then open http://localhost:8000.

## What's here

- `app.py` — FastAPI backend. `/api/compare?q=<medicine>` runs the same
  scraping + matching logic as the original CLI tool, cached in memory for
  a few minutes per query.
- `scrapers/` — one module per platform, each hitting that platform's own
  public JSON endpoint directly (no login needed for any of them).
- `normalize/match.py` — parses a scraped title into brand/strength/pack
  size/form and scores two parsed titles against each other, with a hard
  brand-name gate (many real Indian pharmacy brands share a name prefix —
  see the comments in that file for the actual bugs this caught).
- `static/index.html` — the frontend. Plain HTML/CSS/JS, mobile-friendly,
  calls `/api/compare` and renders the result.

## Deploying

Deployed on Render as a Python web service:
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`

## Validation

```sh
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Provider searches run concurrently; an unavailable provider is reported separately
and does not hide results from working providers. Only positive, finite prices
for in-stock matched listings can win the lowest listed price. Search results
are cached for 180 seconds with a bounded cache; partial outages are not cached.

Matching is deliberately conservative: exact brand/variant, known dosage form,
pack size and unit-aware strength are required. Missing units or unsupported
concentrations remain unconfirmed in “Other results”. Title matching is not
clinical verification and cannot identify therapeutic substitutes. A complete
listing may anchor the comparison when the first provider lacks identity fields;
the selected reference is always shown.

These are undocumented website endpoints, not contracted data feeds. Availability
can change. Prices are indicative: 1mg defaults to Pune; this app does not verify
user-local stock or include delivery fees, personalised discounts or checkout
charges. Confirm every listing with the pharmacy. The UI shows the retrieval
time and links to the original listing.
