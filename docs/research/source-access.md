# Source access: how each candidate site can be collected

Researched 2026-11-20/21. Method: official docs, ToS and robots.txt, plus live probes from a plain HTTP client (`curl` with an honest, non-browser User-Agent: `Mozilla/5.0 (compatible; research-probe/0.1)`). No browser spoofing, no sign-ups, no forms. "Probe" in the notes means one or a few requests from that client. Anything I could not confirm is marked **unverified**.

Context: single-user, personal, non-commercial collector. None of this is legal advice.

## Verdict key

- **EASY**: official API, RSS or public JSON; usage permitted
- **MEDIUM**: plain HTML (or undocumented JSON) works; no explicit ban; no anti-bot wall
- **HARD**: anti-bot wall, login wall, or ToS/robots explicitly forbids
- **HARD-ToS (tech EASY)**: technically trivial (public JSON works with curl), but the site's terms explicitly ban automated access. This is a policy call for you, not a technical one.
- **SKIP**: anti-bot wall *and* explicit ToS ban *and* no usable API

## Summary table

| Site | Method | Auth | Limits | Anti-bot (plain HTTP client) | ToS / robots stance | Verdict |
|---|---|---|---|---|---|---|
| jira.atlassian.com (suggestions) | Jira REST API v2 (`/rest/api/2/search`, JQL) | None (anonymous) | Undocumented; maxResults capped at 1000/page | None; JSON returned | AUP bans scraping "by any means other than our publicly supported interfaces"; REST is a supported interface. robots only blocks UI JQL URLs | **EASY** |
| Xero Product Ideas (UserVoice) | HTML (server-rendered), ~20 ideas/page | None | Undocumented | None | robots: "Please don't crawl the API" (`/api/` disallowed). Xero ToS: no explicit scraping clause | **MEDIUM** |
| HubSpot Community (incl. Ideas) | Discourse (`.json`), but behind Cloudflare | None | Discourse defaults (unverified for this site) | **Cloudflare "Just a moment" challenge (403)** on all pages, incl. `.json` | `/tos` blocked (403); unverified | **HARD** |
| Salesforce IdeaExchange | Salesforce Experience Cloud (Lightning) SPA | None to view (unverified) | Undocumented | Heavy JS; HTML is a "Loading" shell | Site terms: "You may not use content from our Sites unless you obtain permission" | **HARD** |
| Microsoft Feedback Portal | Power Pages React SPA | None to view (unverified) | Undocumented | Heavy JS ("You need to enable JavaScript") | MS ToU: may not obtain info "through any means not intentionally made available" | **HARD** |
| Canny boards (*.canny.io) | HTML with embedded JSON state (`__data`) | None. Official API needs the board owner's secret key | Official API 5–20 req/s by owner plan (not usable by you) | None | robots: allow all. Canny ToS: no automation clause; general "may not ... download ... copy ... Content" | **MEDIUM** |
| UserVoice boards (*.uservoice.com) | HTML (same as Xero) | None. API needs owner key | Undocumented | None | robots disallows `/api/`. UserVoice ToS: no visitor scraping clause | **MEDIUM** |
| Featurebase boards | Undocumented portal JSON (`/api/v1/submission`) works unauthenticated; official API needs owner's key | None (undocumented endpoint) | Undocumented | None | robots: `Allow: /`. Featurebase ToS: unverified | **MEDIUM** |
| QuickBooks (Intuit) Community | HTML (Khoros, per community thread; unverified) | None to read | Undocumented | **Plain curl blocked**: HTTP/2 stream reset, HTTP/1.1 timeout (bot management) | Intuit ToS bans "scraping, accessing, or downloading content that doesn't belong to you" | **SKIP** |
| Shopify Community | Discourse `.json` (works) | None | Discourse defaults: 200 req/min, 50 req/10 s per IP | None | `/tos` resolves to Shopify ToS: "not ... using any robot, spider, scraper, or other automated means" | **HARD-ToS (tech EASY)** |
| Zapier Community | Gainsight/inSided: RSS `/feed/topics` (100 items) + server-rendered HTML | None | Undocumented | None | Zapier ToS: no scraping clause (AUP not checked). robots blocks search/members only | **EASY** (RSS) / MEDIUM (history) |
| Make Community | Discourse `.json` (works) | None | Discourse defaults | None | Forum `/tos`: "You may not automate access to the forum ... with a web crawler ... or other computer program that is not a web browser" | **HARD-ToS (tech EASY)** |
| n8n Community | Discourse `.json` incl. `search.json` (works) | None | Discourse defaults | None | Same default Discourse forum ToS clause as Make | **HARD-ToS (tech EASY)** |
| Spiceworks Community | Discourse `.json` (works) | None | Discourse defaults | None for curl; robots blocks Scrapy/HTTrack/AI bots | Ziff Davis ToU bans "any robot, spider ... to crawl, scrape ... or index any portion of the Services"; listed on TollBit pay-per-crawl marketplace | **HARD-ToS (tech EASY)** |
| BiggerPockets forums | RSS `/topics/latest.rss` (100 items; listed in robots.txt) + HTML | None | Undocumented | None | ToS: "any unauthorized scraping of Content on this Site is strictly prohibited"; "personal, educational, noncommercial use only" | **EASY** (RSS, recent only) / HARD-ToS (HTML backfill) |
| ContractorTalk | XenForo RSS `/forums/-/index.rss` (35 items) works; HTML does not | None | Undocumented | **HTML: JS proof-of-work challenge (HTTP 202)**. AI-agent UAs redirected to TollBit (HTTP 402 Payment Required) | VerticalScope ToU text unverified; VerticalScope sued AI scrapers (May 2026) | **MEDIUM** (RSS only) / HARD (HTML) |
| Lobsters | RSS (officially public) + `.json` endpoints (work) | None | robots `Crawl-delay: 1` | None | robots: `User-agent: *` → `Disallow: /` (only named search engines allowed); `Content-Signal: ai-input=no` | **EASY** (RSS) / MEDIUM (JSON, robots-disallowed) |
| Indie Hackers | HTML/JS | None | Undocumented | **Cloudflare challenge (403)** | robots allows general crawl (blocks AI bots); ToS page blocked (403), unverified | **HARD** |
| Shopify App Store reviews | Server-rendered HTML, `?page=N` (e.g. 347 pages for a big app) | None | Undocumented | None | robots allows (blocks `*q=*`). No public reviews API. Whether Shopify's ToS robot ban covers apps.shopify.com: unverified | **MEDIUM** |
| Chrome Web Store reviews | JS-rendered; no public reviews API | None | n/a | Detail page loads; reviews need JS | robots: `Disallow: /detail/*/reviews`; Google ToS bans automated access "in violation of ... robots.txt" | **HARD** |
| WordPress.org plugins (reviews + support) | Official `api.wordpress.org` JSON + RSS (`/support/plugin/<slug>/feed/`, `/reviews/feed/`, 30 items) + HTML pages | None | Undocumented | None | robots allows nearly everything (even AI bots explicitly allowed) | **EASY** |
| Apple App Store reviews RSS | Legacy JSON/RSS `itunes.apple.com/<cc>/rss/customerreviews/page=N/id=<id>/sortby=mostrecent/json` | None | 50 reviews/page, **10 pages max (page 11 → HTTP 400)** = 500 most recent per country | None | itunes.apple.com robots: `Disallow: /*/rss/*`. Not in Apple's current RSS generator (undocumented legacy) | **MEDIUM** (tech EASY) |
| Google Play reviews | HTML shows ~3 reviews; rest via JS `batchexecute`. Play Developer API = own apps only | n/a (API: OAuth, own apps) | API: own apps, last 7 days, 200 GET/h | Page loads; reviews need JS | robots: `Disallow: /store/getreviews`; Google ToS bans automated access that violates robots.txt | **HARD** |
| G2 | HTML; API only for G2 customers (tokens) | Paid/customer token | Undocumented | **DataDome block (403 "Please enable JS")** | ToS s.8 bans "robots, crawlers, scrapers, headless browsers ... or other automated means" and bypassing CAPTCHAs/robots.txt | **SKIP** |
| Capterra | HTML; no public API | n/a | n/a | **Cloudflare challenge (403)** | General User Terms ban scripts, spiders, robots, crawlers, data-mining tools (via search snippet; page 403'd) | **SKIP** |
| Trustpilot | HTML; API for Trustpilot Business customers (API module add-on) | Paid business API key | API: 200k–unlimited calls/yr by tier | **AWS WAF challenge (403 "Verifying your connection")** | robots: `User-agent: *` → `Disallow: /`. ToS v8.0 bans "text mining, data mining or web scraping ... for any purpose without our express permission" | **SKIP** |
| Product Hunt | Official GraphQL API v2 | Free developer token (needs an account) or OAuth | 6,250 complexity points / 15 min (GraphQL); 450 req / 15 min (other) | Website: Cloudflare challenge. API: no challenge | API docs: "must not be used for commercial purposes" by default; personal use OK | **EASY** |
| Acquire.com | Public teaser pages `app.acquire.com/public/...` (~1,243 in sitemap), server-rendered; full listing needs login | Login for details | Undocumented | None on teasers | ToS bans "any robot, spider or other automatic device ... to access the Website for any purpose" | **HARD** |
| Flippa | HTML; JSON API `/v3/listings` now needs auth | `/v3` returns 401 "Authorization header empty" | Undocumented | **Cloudflare challenge on every page, even robots.txt** | ToS page 403'd; unverified | **SKIP** |
| Bluesky | AT Protocol XRPC (`app.bsky.feed.searchPosts`) | None for page 1; account (app password) needed for pagination (likely) | robots: "Up to a handful concurrent requests should be ok"; 429 = back off | `public.api.bsky.app` search → 403; `api.bsky.app` page 1 → 200, **with `cursor` → 403** | robots explicitly allows crawling the public API | **EASY** (with free account; unverified) |
| YouTube Data API (comments) | Official REST API | Free API key | 10,000 units/day; `commentThreads.list` = 1 unit; `search.list` capped at **100 calls/day** | n/a | Policies ban scraping; non-authorized data may be stored ≤30 days | **EASY** |
| Mastodon (mastodon.social) | REST API + RSS (`/tags/<tag>.rss`) | None for hashtag timelines; account for public/live timeline and search | 300 req / 5 min per IP and per account | None | robots blocks AI crawlers and the Scrapy UA. Instance ToS: no scraping clause | **EASY** (hashtag-based only) |
| Upwork job posts | GraphQL API by application only; RSS removed 2024-08-20 | API key needs approval (JSS ≥90%, use-case review) | 40,000 req/day (API key condition) | **Cloudflare challenge (403)** | Help centre: scraping public data off-limits "even with an API key" | **SKIP** |

## Per-site notes

### Vendor feature-request / ideas portals

**jira.atlassian.com**
- Probe: `GET /rest/api/2/search?jql=project=JRACLOUD AND issuetype=Suggestion ORDER BY votes DESC` returned JSON anonymously. Total: 28,126 JRACLOUD suggestions. Top result: "A dark theme for Jira Cloud" (8,084 votes). Oldest seen: created 2004. Requesting `maxResults=5000` was capped at 1000. `fields=comment` works per issue. No rate-limit headers seen; limits are **unverified**.
- Projects follow `JRACLOUD`/`JRASERVER`, `CONFCLOUD`/`CONFSERVER` and so on. Use `issuetype=Suggestion` to filter.
- robots.txt disallows `/issues/?jql=*`, `/sr/`, `/si/`, `/charts`, attachments and the dashboard, but not `/rest/`.
- AUP (archived 2020-11-09 version; I couldn't load the current page, so current wording is **unverified**) bans using robots "to send more request messages to our servers than a human could reasonably send", and "Accessing or searching any part of the services by any means other than our publicly supported interfaces (for example, 'scraping')".
- History: full (back to 2002–2004).
- Sources: https://jira.atlassian.com/robots.txt · https://www.atlassian.com/legal/archives/acceptable-use-policy/acceptable-use-policy11092020

**Xero Product Ideas (productideas.xero.com, UserVoice)**
- Server-rendered HTML. A forum page (e.g. `/forums/967115-invoices-quotes`) has about 21 idea links and pagination to `page=53`, so full history is reachable by paging. No RSS/Atom links found. Old UserVoice `/api/v1` is robots-disallowed and needs an admin key.
- Xero Terms: no scraping clause. The closest are "Access any system without permission" and "Repackage, resell, or sublicense any leads or data accessed through our services".
- Sources: https://productideas.xero.com/robots.txt · https://www.xero.com/legal/terms/

**HubSpot Community / Ideas (community.hubspot.com)**
- Footer says "Powered by Discourse". A migration launched about 2026-06-15; ideas were migrated. A plain client gets a Cloudflare "Just a moment..." 403 on both `/` and `/latest.json`. (Anthropic's fetcher got through, so Cloudflare probably allow-lists known agents. A personal collector would not get through.)
- robots.txt has the standard Discourse rules (`/search` and `*.rss` disallowed). `/tos` returned 403: **unverified**.
- Source: https://community.hubspot.com/t/hubspot/150078

**Salesforce IdeaExchange (ideas.salesforce.com)**
- Lightning/Experience Cloud. Fetched HTML is a "Loading"/"Sorry to interrupt" shell and needs a headless browser. No public API or export found; the `Idea` sObject in Salesforce's API is for an org's own data. robots.txt only blocks PetalBot.
- Website terms: "You may not use content from our Sites unless you obtain permission from us or its owner."
- Sources: https://ideas.salesforce.com/s/faq · https://www.salesforce.com/company/legal/sfdc-website-terms-of-service/

**Microsoft Feedback Portal (feedbackportal.microsoft.com)**
- Power Pages React app (`content.powerapps.com/.../cmty/...main.js`). `<noscript>` says "You need to enable JavaScript". No documented public API. Whether the Power Pages `/_api` is open is **unverified**. robots.txt returns 404.
- MS Terms of Use: "You may not obtain or attempt to obtain any materials or information through any means not intentionally made available."
- Source: https://www.microsoft.com/en-us/legal/terms-of-use

**Canny (public *.canny.io boards)**
- `feedback.canny.io/feature-requests` is server-rendered with a `__data` JSON blob containing post titles. Pagination and depth beyond the first load: **unverified**.
- The official API needs the company's secret `apiKey`, so it can't be used on other people's boards. Limits are per key by plan: Free 5/s, 100/min, 1,000/h; Business 20/s, 600/min, 15,000/h.
- Canny terms have no automation clause. Individual Canny customers may have their own terms: **unverified**.
- Sources: https://developers.canny.io/api-reference · https://canny.io/terms

**UserVoice (generic)**
- Same platform as Xero. `ideas.uservoice.com` now returns 410 Gone.
- UserVoice ToS only binds account holders ("not to copy ... the UserVoice Platform or API"). End-user terms: **unverified**.
- Source: https://www.uservoice.com/terms-of-service

**Featurebase (generic)**
- `feedback.featurebase.app/api/v1/submission?page=1&limit=2` returned post JSON (title, content, upvotes) with no auth. This is the portal's own undocumented endpoint and could change without notice.
- The official REST API (`/v2/posts`, cursor pagination, 1–100 per page) needs the board owner's `Bearer sk_...` key. robots.txt: `Allow: /`.
- Source: https://docs.featurebase.app/rest-api/posts

### Vendor / community forums

**Discourse sites in general (Shopify, Make, n8n, Spiceworks, HubSpot)**
- `/latest.json`, `/c/<cat>/<id>.json`, `/t/<id>.json` and `/search.json?q=` all return JSON to a plain client (except HubSpot). `?page=N` on `latest.json` walks back through all topics, so history is complete.
- Default per-IP limits: 200 req/min and 50 req/10 s, mode `block`. They aren't adjustable on hosted Free/Starter/Pro/Business plans.
- Default robots.txt disallows `/search` and `*.rss` paths for `*`.
- The default forum `/tos` template, used by Make and n8n, says: "You may not automate access to the forum, or monitor the forum, such as with a web crawler, browser plug-in or add-on, or other computer program that is not a web browser. You may crawl the forum to index it for a publicly available search engine, if you run one."
- Discourse ID user terms (2026-08-11) ban "Scrape, crawl, or use automated means to access a Site in ways that exceed normal use" and "violate robots.txt".
- Sources: https://meta.discourse.org/t/available-settings-for-global-rate-limits-and-throttling/78612 · https://community.n8n.io/tos · https://community.make.com/tos · https://www.discourse.org/user-terms

**QuickBooks Community (quickbooks.intuit.com/learn-support)**
- Plain curl failed: HTTP/2 "stream not closed cleanly: INTERNAL_ERROR", then an HTTP/1.1 timeout. robots.txt couldn't be fetched either. That looks like bot management (vendor **unverified**).
- No native RSS. Users asked for one; a user cited a Khoros feed URL pattern, but it wasn't confirmed to work.
- Intuit ToS prohibited uses: "including scraping, accessing, or downloading content that doesn't belong to you".
- Sources: https://quickbooks.intuit.com/learn-support/en-us/other-questions/community-rss-feed/00/743472 · https://www.intuit.com/legal/terms/en-us/website/

**Shopify Community (community.shopify.com)**
- Discourse. `latest.json` works. `/tos` serves the Shopify Terms of Service: "You agree not to access the Services or monitor any material or information from the Services using any robot, spider, scraper, or other automated means."
- Users on the separate developer forum (community.shopify.dev) report aggressive 429s at 100–200 req/hour. Whether that applies to community.shopify.com is **unverified**.
- Sources: https://community.shopify.com/robots.txt · https://community.shopify.com/tos · https://community.shopify.dev/t/bug-429-page-errors-are-too-aggressive/14325

**Zapier Community (community.zapier.com)**
- "Powered by Gainsight" (inSided). `/feed/topics` is RSS with 100 latest topics (`/rss` is a 404). Feature Requests category: `/feature-requests-145`, server-rendered.
- Gainsight's developer API exists but needs the community owner's credentials.
- robots.txt disallows `/members/`, search and sort params, and has `Content-Signal: ai-train=no, search=yes, ai-input=yes`.
- Zapier ToS: no scraping clause; only "circumvent its access restrictions". The AUP was not checked (**unverified**).
- Sources: https://community.zapier.com/robots.txt · https://communities.gainsight.com/customer-communities-cc-17/rss-feed-20417 · https://zapier.com/legal/terms-of-service

**Make (community.make.com) and n8n (community.n8n.io)**
- Discourse JSON works, including n8n `search.json`. Both use the default forum ToS clause quoted above.

### Niche professional forums

**Spiceworks Community (community.spiceworks.com)**
- Discourse JSON works with plain curl. robots.txt blocks GPTBot, ClaudeBot, CCBot, Scrapy, HTTrack, ia_archiver and others.
- `/tos` redirects to the Ziff Davis ToU: "(ii) use any robot, spider, site search and/or retrieval application, or other device to crawl, scrape, database scrape, screen scrape, harvest, gather, extract, retrieve or index any portion of the Services". It also bans use for "data mining" and "TDM".
- Listed on TollBit's AI-licensing marketplace.
- Sources: https://community.spiceworks.com/robots.txt · https://www.ziffdavis.com/terms-of-use · https://tollbit.com/marketplace/spice-works-community/

**BiggerPockets (www.biggerpockets.com/forums)**
- HTML forum loads (200). robots.txt lists `topics/latest.rss` and `topics/recently_updated.rss` as sitemaps; each has 100 items.
- ToS: "any unauthorized scraping of Content on this Site is strictly prohibited"; "You will not incorporate and/or store the Content within your internal systems"; "personal, educational, noncommercial use only".
- The site publishes the RSS, so reading it looks like intended use. Backfilling via HTML does not.
- Sources: https://www.biggerpockets.com/robots.txt · https://www.biggerpockets.com/terms

**ContractorTalk (www.contractortalk.com, VerticalScope)**
- HTML: a plain client gets HTTP 202 with a JS proof-of-work page (`POW_CHALLENGE_DATA`, webdriver/WebGL checks).
- XenForo RSS `/forums/-/index.rss` returned 200 with 35 items. Per-forum `.rss` should work too but is **unverified**.
- An AI agent's fetch was redirected to `tollbit.contractortalk.com`, which returned **402 Payment Required**.
- robots.txt: `Allow: /` for `*` (search, login and account excepted); blocks a long list of AI bots.
- VerticalScope ToU text: **unverified**. VerticalScope filed suit in May 2026 alleging scraping in breach of its ToU.
- Sources: https://www.contractortalk.com/robots.txt · https://www.litigate.com/assets/uploads/20260526-162717-3674-Statement-of-Claim-Plaintiffs-VerticalScope-and-Second-Media-25-MAY-2026-Filed.pdf

### Tech / founder communities

**Lobsters (lobste.rs)**
- About page: "Per-tag, multi-tag and site-wide RSS feeds are available to the public." `/rss` returns 25 items. `/hottest.json` and `/newest.json` also work (story `.json` includes comments).
- robots.txt allows only named search engines. Everyone else gets `User-agent: *`, `Crawl-delay: 1`, `Disallow: /`, plus `Content-Signal: ai-input=no, ai-train=no, search=yes`. Use RSS; JSON backfill goes against robots.
- Sources: https://lobste.rs/about · https://lobste.rs/robots.txt

**Indie Hackers**
- Cloudflare "Just a moment" 403 for a plain client. robots.txt allows general crawling (disallows `/api/`) and blocks about 20 AI bots. ToS page returned 403: **unverified**. No official RSS found (**unverified**).

### Review sites / marketplaces

**Shopify App Store (apps.shopify.com/<app>/reviews)**
- Server-rendered: about 10 reviews per page and `?page=N` links (page 347 for Klaviyo), so full history is reachable.
- No public read API. Staff say reviews are only available in the Partner Dashboard; the App Bridge Reviews API only asks merchants to leave a review.
- robots.txt disallows `*q=*` (search) and login/internal paths. Whether the Shopify ToS robot clause covers the App Store: **unverified**.
- Sources: https://apps.shopify.com/robots.txt · https://community.shopify.com/t/is-there-api-for-app-store-reviews/209135

**Chrome Web Store**
- The detail page returns 200 with the rating summary. Review text is loaded by JS from `/detail/*/reviews`, which robots.txt disallows.
- No public reviews API ("there is no public API to get user review information", per a Chromium-extensions group reply).
- Google ToS: no "using automated means to access content from any of our services in violation of the machine-readable instructions ... robots.txt".
- Sources: https://chromewebstore.google.com/robots.txt · https://policies.google.com/terms · https://groups.google.com/a/chromium.org/g/chromium-extensions/c/nrfmHJb9bgs

**WordPress.org**
- `api.wordpress.org/plugins/info/1.2/?action=plugin_information&request[slug]=<slug>` returns JSON.
- RSS: `/support/plugin/<slug>/feed/` and `/support/plugin/<slug>/reviews/feed/`, 30 items each. For older items, page through the HTML at `/support/plugin/<slug>/reviews/page/N/` (I only tested the feeds).
- robots.txt disallows only `/search`, `/?s=` and `/plugins/search/`, and explicitly allows GPTBot, ClaudeBot and CCBot. API rate limits: **unverified**.
- Sources: https://wordpress.org/robots.txt · https://meta.trac.wordpress.org/ticket/1999

**Apple App Store customer reviews**
- Live test (2026-11-21): pages 1–10 each returned 50 entries; page 11 returned HTTP 400. That's 500 most-recent reviews per app per country storefront. Run each storefront (`/us/`, `/gb/`, ...) separately to get more.
- itunes.apple.com robots.txt has `Disallow: /*/rss/*`. The feed doesn't appear in Apple's current RSS generator (rss.marketingtools.apple.com, which offers charts only), so it's undocumented and could be switched off.
- The App Store Connect API only covers your own apps.
- Sources: https://itunes.apple.com/robots.txt · https://rss.marketingtools.apple.com/ · https://developer.apple.com/forums/thread/719240

**Google Play**
- The app page HTML contains about 3 reviews. The rest load via JS `batchexecute`. robots.txt has `Disallow: /store/getreviews` (and `/store/xhr`).
- Play Developer API: "You can retrieve only the reviews that users have created or modified within the last week". Production versions of your own app only; 200 GET/hour.
- Sources: https://play.google.com/robots.txt · https://developers.google.com/android-publisher/reply-to-reviews · https://policies.google.com/terms

**G2**
- 403 with "Please enable JS and disable any ad blocker" (DataDome).
- Terms s.8 ban "robots, crawlers, scrapers, headless browsers, data-mining tools, or other automated means" and bypassing "access controls, rate limits ... CAPTCHAs ... robots.txt directives".
- The API (data.g2.com) uses tokens from G2 customer/partner accounts.
- Sources: https://legal.g2.com/terms-of-use · https://documentation.g2.com/docs/developer-portal

**Capterra**
- Cloudflare challenge (403). The legal page also returned 403 to my fetcher.
- A search snippet of the Capterra General User Terms shows a ban on collecting content via "automated scripts, spiders, robots, crawlers, data-mining tools". I couldn't load the primary page, so treat this as near-verified.
- Source: https://capterra.com/legal/general-user-terms_lessprioritymarkets/

**Trustpilot**
- AWS WAF "Verifying your connection" (403). robots.txt allows specific named bots; for `User-agent: *` it is `Disallow: /`.
- Consumer Terms v8.0 (effective 2026-10-01): "You must not access, search or collect content from our platform by any means (automated or otherwise)..." and "...data mining or web scraping of our platform for any purpose without our express permission."
- API: Business Units public reviews endpoint with an API key. The API module is an add-on for Trustpilot for Business customers (200k / 1M / unlimited calls per year by tier).
- Sources: https://corporate.trustpilot.com/legal/for-reviewers/terms-of-use-for-consumers · https://developers.trustpilot.com/api-module-overview/

### Launch / market

**Product Hunt**
- GraphQL `api.producthunt.com/v2/api/graphql`. A developer token "does not expire, linked to your account"; client-credentials OAuth covers public data.
- Limits: "quota of 6250 complexity points for every 15 minutes" (GraphQL); 450 req / 15 min for other endpoints.
- "By default the Product Hunt API must not be used for commercial purposes."
- The website itself is behind a Cloudflare challenge. The API endpoint returns 401 JSON without a token, so it isn't blocked. History depth via `postedBefore`/`postedAfter`: **unverified**.
- Sources: https://api.producthunt.com/v2/docs · https://api.producthunt.com/v2/docs/rate_limits/headers

**Acquire.com**
- `app.acquire.com/sitemap.xml` lists about 1,243 `/public/<id>-<slug>` teaser pages. They're server-rendered with headline, asking price and multiples; full details need a buyer login.
- robots.txt for app.acquire.com allows only `/public/`, `/startup/` and the sitemap.
- ToS: "Use any robot, spider or other automatic device, process or means to access the Website for any purpose" is prohibited, as is "any commercial purposes".
- Source: https://acquire.com/terms/

**Flippa**
- Cloudflare challenge on everything, including robots.txt. `flippa.com/v3/listings`, which third-party tools describe as the public API, now returns `401 "Authorization header empty."` ToS page returned 403: **unverified**.

### Social

**Bluesky**
- `public.api.bsky.app/xrpc/app.bsky.feed.searchPosts` → 403.
- `api.bsky.app/...searchPosts?q=` → 200 for page 1. The same call with `cursor=` → 403 "Request forbidden by administrative rules". A GitHub issue shows staff blaming "deliberately returning 403 to decrease load" (2025) and reports of 403s again in Aug 2026.
- An authenticated session with a free account and app password most likely lifts this. **Unverified**: I didn't sign up.
- Non-search reads work anonymously (`getAuthorFeed`, `searchActors`). A firehose/Jetstream also exists for real-time collection (not tested).
- robots.txt for public.api.bsky.app: "Crawling the public parts of the API is allowed. HTTP 429 ('backoff') status codes are used for rate-limiting. Up to a handful concurrent requests should be ok." The rate-limit docs page wouldn't load (redirects to bsky.network), so numbers are **unverified**.
- Sources: https://public.api.bsky.app/robots.txt · https://bsky.app/robots.txt · https://github.com/bluesky-social/bsky-docs/issues/332

**YouTube Data API (comments)**
- Default quota is 10,000 units/day. `commentThreads.list` and `comments.list` cost 1 unit each (up to 100 per page), so full comment history per video is reachable.
- **`search.list` now has its own default limit: "100 calls per day"**. Finding videos by keyword is the bottleneck, not reading comments.
- Developer Policies: may not "scrape YouTube Applications". Non-authorized API data may be stored "not longer than 30 calendar days" before refresh or delete.
- Sources: https://developers.google.com/youtube/v3/determine_quota_cost · https://developers.google.com/youtube/terms/developer-policies

**Mastodon (mastodon.social)**
- Instance config (`/api/v2/instance`, v5.0.0-nightly): `live_feeds: disabled` (public timeline → 422 "requires an authenticated user"); `hashtag_feeds: public`.
- `/api/v1/timelines/tag/<tag>` and `/tags/<tag>.rss` (20 items) work anonymously. Anonymous `/api/v2/search` returned empty statuses.
- Full-text search only covers posts from accounts that opted in (Mastodon 4.2+). Hashtags are effectively the only discovery route.
- Limits: 300 req / 5 min per IP and per account.
- Instance ToS (2026-08-31, read via API) has no scraping clause. robots.txt: "You are not allowed to use the contents of this website for LLM training"; blocks AI bots and `Scrapy`.
- Sources: https://docs.joinmastodon.org/api/rate-limits/ · https://mastodon.social/robots.txt · https://blog.joinmastodon.org/2023/09/mastodon-4.2

### Work marketplaces

**Upwork**
- RSS discontinued "after August 20, 2024". HTML returns a Cloudflare 403. robots.txt disallows `/*/jobs/search*`, `/rss/` and `/ab/`.
- API key: needs approval. Requirements include JSS ≥90%, a use description, staying under 40,000 req/day, and "personal and internal use".
- Help centre: "Even with an API key, some actions remain off-limits ... scraping public or private data".
- Sources: https://support.upwork.com/hc/en-us/articles/52052528243731-RSS-deprecation · https://support.upwork.com/hc/en-us/articles/115015857647-Request-an-API-Key · https://support.upwork.com/hc/en-us/articles/43342677368467-Use-bots-and-other-automation-properly
