# Netflix Cookie Checker Bot

A Telegram bot that validates Netflix cookies live against Netflix's servers. Includes a web status dashboard.

## Run & Operate

- **Workflow**: `cd scripts/netflix-bot && python bot.py`
- **Required env var**: `TELEGRAM_BOT_TOKEN` (set as a Secret in Replit)
- The bot starts the Flask dashboard on port 5000 as a background thread, then enters the Telegram polling loop.

## Stack

- Python 3.11
- `python-telegram-bot==21.6` (async Telegram API)
- `curl_cffi>=0.7.0` — Chrome124 TLS fingerprint impersonation (critical for Netflix)
- `flask==3.1.3` — status dashboard
- `requests==2.32.3` — fallback HTTP client

## Where things live

```
scripts/netflix-bot/
├── bot.py        # Telegram handlers, formatters, bulk check logic, ZIP export
├── checker.py    # Cookie parsing, Netflix HTTP validation, NFToken generation
├── stats.py      # Thread-safe in-memory stats tracker
├── dashboard.py  # Flask status dashboard (port 5000)
└── requirements.txt
```

## Architecture decisions

- **curl_cffi over requests**: Netflix bot-detection inspects the TLS fingerprint. Python's `requests` library sends a Python/OpenSSL fingerprint that Netflix flags, causing valid cookies to appear invalid. `curl_cffi` impersonates Chrome124's exact TLS stack.
- **Single endpoint**: Only `/account/membership` is fetched (+ `/browse` fallback in single mode). All account data is extracted from the reactContext JSON embedded in that page.
- **Bulk mode**: `BULK_CONCURRENCY=8` with 0.5s inter-batch sleep keeps request rate below Netflix's IP-level throttle. NFTokens are skipped in bulk and generated on-demand via button.
- **In-memory stats**: Stats reset on bot restart. No database needed for the dashboard.
- **One session per check**: Each `check_cookie()` call gets its own `curl_cffi.Session` to avoid cookie-jar cross-contamination between users.

## Product

- Send a `.txt`, `.json`, or `.zip` cookie file → bot checks each Netflix cookie live
- Single-check mode: full account card (plan, billing, country, payment, profiles, login links)
- Bulk mode: progress bar, hit/invalid/error summary, ZIP of all hits at the end
- Status dashboard at the webview URL (auto-refreshes every 10s)
- Supported formats: Netscape, JSON array, pipe-combo, CookieCheckerPro hit files, ZIP bundles

## User preferences

- Keep log level at WARNING — avoid verbose output on hosted platforms
- Full mode (default) shows all fields; Basic mode shows a clean summary card

## Gotchas

- **TELEGRAM_BOT_TOKEN must be set as a Secret** — the bot raises `ValueError` on startup if missing
- Netflix returns HTTP 200 with a login page HTML (not always a 302) for invalid cookies — must check body markers, not just URL
- Rate limiting: Netflix 429s are handled with `Retry-After` backoff in `_fetch_account()`
- `curl_cffi` must be installed; `requests` is the fallback but will get more false-negatives

## Pointers

- Cookie validation logic: `scripts/netflix-bot/checker.py` → `check_cookie()`
- NFToken generation: `checker.py` → `generate_nftoken()`
- Bulk processing: `bot.py` → `process_cookie_sets()`
