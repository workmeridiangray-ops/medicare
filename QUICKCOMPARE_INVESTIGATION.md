# How QuickCompare.in Works — Investigation Notes

*Written 2026-11-14, as part of the MedCompare project research.*

## The question

QuickCompare.in shows live prices for the same product across Blinkit, Zepto, Swiggy Instamart, BigBasket, JioMart, and more. MedCompare hit hard bot-protection walls trying to reach Blinkit (Cloudflare Bot Management) and Zepto (AWS WAF + CSRF tokens) directly. So: how does QuickCompare actually pull this off?

## What we found, step by step

### 1. The frontend never talks to Blinkit/Zepto directly

Using the browser's Network tab and `performance.getEntriesByType('resource')` (the standard network-request list missed these calls — only `javascript_tool` surfaced them), every real data request from quickcompare.in goes to **one domain they control**: `api.quickcompare.in`.

Example calls captured:
```
https://api.quickcompare.in/qc?type=qcitemsingle&qcitemid=QCP787245887C&platform=Zepto&lat=...&lon=...
https://api.quickcompare.in/qc?type=groupsearch&query=Curd&lat=...&lon=...&pincode=713211
```

The page's `<head>` even preconnects to it on load:
```html
<link rel="preconnect" href="https://api.quickcompare.in" crossorigin />
```

### 2. Their own API blocks direct/automated calls too

Calling `api.quickcompare.in` directly (outside their real frontend session) returns:

```json
HTTP/2 403
{
  "error": "automated_access_blocked",
  "message": "We've noticed automated use of the QuickCompare API. If you're building on QuickCompare data, we'd be happy to support you officially. Use QuickCommerce API (https://quickcommerceapi.com) for real-time prices, availability and delivery times across quick-commerce apps. For bulk or custom data, email connect@quickcompare.in or WhatsApp +91 77957 23048.",
  "official_api": "https://quickcommerceapi.com",
  "contact": {
    "email": "connect@quickcompare.in",
    "whatsapp": "+91 77957 23048",
    "whatsapp_link": "https://wa.me/917795723048"
  }
}
```

This is the key finding: **QuickCompare itself is likely a paying customer of a third-party data vendor** (`quickcommerceapi.com`, or a similar provider), not necessarily the builder of the bot-defeating scraping infrastructure. Their own backend is probably a thin layer that calls that vendor's API and re-serves the results, with their own bot-detection layered on top to stop others scraping *them* in turn.

### 3. The shipped frontend code is just a normal React/Vite app

Checked DevTools → Sources: all the JS chunks are ordinary page/component bundles (`HomePage-*.js`, `GroupListPage-*.js`, `AmazonDealCard-*.js`, `CartComparisonPage-*.js`, icon components, etc.) — standard webpack/Vite code-splitting, no scraping logic anywhere client-side. That's expected: a browser can't run anything that would need its own IP/session infrastructure to look "real" to Blinkit/Zepto — that only makes sense running server-side, which is why it's invisible from the browser no matter how deep we dig.

## How a vendor like quickcommerceapi.com likely does it

(Described at a conceptual level — not something being built here.)

- **Large pools of real browser sessions** running in parallel, each establishing genuine cookies/tokens the way a real phone app would, rather than hitting raw endpoints cold.
- **Residential/mobile proxy networks** so requests don't all come from obvious datacenter IPs — the single biggest signal bot-protection systems key on.
- **Constant reverse-engineering upkeep** — platforms change their token/signing schemes specifically to break scrapers, so this requires an ongoing maintenance team, not a one-time build.
- **Possibly some official/rate-limited partner access** once an aggregator has enough scale that platforms find it easier to tolerate with limits than fight indefinitely.

This is a genuinely expensive, dedicated infrastructure business — which is why it's sold as a paid B2B API rather than given away.

## Conclusion for MedCompare

- **1mg / PharmEasy / Netmeds** — stay in MedCompare as-is. These have open, unauthenticated JSON endpoints; no proxy/browser-farm infrastructure needed. This is the same ethical line held throughout the project: scrape an open endpoint, don't defeat a wall.
- **Apollo 24/7** — excluded (auth-gated GraphQL).
- **Truemeds** — excluded (Akamai bot protection).
- **Blinkit / Zepto / Instamart** — not pursued directly (Cloudflare Bot Management / AWS WAF). The only legitimate path in would be subscribing to a paid, licensed data vendor (like quickcommerceapi.com or similar) and wrapping their API — a business/cost decision for later, not something built by reverse-engineering the platforms' protection.

No anti-bot-bypass infrastructure was built or attempted as part of this project.
