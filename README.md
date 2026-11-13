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
