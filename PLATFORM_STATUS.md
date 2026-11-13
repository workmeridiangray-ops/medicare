# Platform scraping status

Reverse-engineered by opening each site in a real browser, inspecting its
network traffic, and independently re-verifying the discovered endpoint with
plain `curl` (no login/cookies) before writing a scraper against it.

## Working (wired into `compare.py`)

| Platform  | Approach                                                                 |
|-----------|---------------------------------------------------------------------------|
| 1mg       | Public JSON search API (`pwa-dweb-api/api/v4/search/all`). No auth needed. |
| PharmEasy | Typeahead search API for candidate slugs, then the product page's embedded `__NEXT_DATA__` JSON for price/stock. No auth needed. |
| Netmeds   | Single JSON search API (Fynd commerce platform) that returns full price/stock per item. No auth needed. |

## Not included, and why

**Apollo 24/7 (apollopharmacy.in)** — The app is a Next.js/React Server
Components page; all data resolution happens server-side via GraphQL
(`api.apollo247.com/graphql`). A direct call to that endpoint, even with
valid session cookies from a logged-in browser, returns `401
UNAUTHENTICATED` — it needs a bearer token issued through their own
auth flow. Getting that token would mean extracting an API key from their
obfuscated JS bundle, which is a materially different (and more clearly
ToS-sensitive) activity than calling an open endpoint. Decided not to pursue
this.

**Truemeds** — Uses Next.js's `/_next/data/<buildId>/<route>.json` data
endpoints, but the whole site sits behind Akamai Bot Manager. A plain
`curl` to that endpoint returns an Akamai edge "Access Denied" block.
Getting past that would mean deliberately defeating anti-bot/WAF
protection, not just calling an unauthenticated public endpoint — a
different and more aggressive thing than what worked for the other three.
Decided not to pursue this.

If either of these platforms publishes a public API, or the user has their
own authenticated access they're comfortable using, this would need to be
revisited — but that's a deliberate choice to make, not a default.
