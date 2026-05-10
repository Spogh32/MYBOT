# Netflix Cookie Checker Bot 🎬

A Telegram bot that validates Netflix cookies live against Netflix's servers, reports full account details, generates NFToken login links, and handles bulk checking with ZIP export.

## Features

- **Universal format support** — Netscape `.txt`, CookieCheckerPro, pipe-combo, JSON, ZIP bundles
- **Live validation** — checks cookies directly against Netflix servers
- **Full account details** — email, password, phone, country 🌍, plan, quality, streams, billing, payment
- **NFToken login links** — generates one-click PC & phone login buttons
- **Bulk checking** — live progress bar, 3× parallel speed, only hits messaged
- **ZIP export** — all hits bundled into a dated ZIP with one Netscape file per account
- **Multi-user safe** — concurrent updates enabled, no shared global state

## Setup

### Requirements
- Python 3.11+
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))

### Run locally

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=your_token_here
python bot.py
```

### Run with Docker

```bash
docker build -t netflix-checker-bot .
docker run -e TELEGRAM_BOT_TOKEN=your_token_here netflix-checker-bot
```

## Supported Cookie Formats

| Format | Example |
|--------|---------|
| Netscape `.txt` | `.netflix.com TRUE / TRUE … NetflixId ct%3D…` |
| CookieCheckerPro | `[user]-[IN]-[Premium]-… .netflix.com\t…` |
| Pipe-combo | `email:pass \| Country = IN \| NetflixId = ct%3D…` |
| JSON | `[{"name":"NetflixId","value":"ct%3D…"}]` |
| ZIP | Drop a ZIP — each `.txt`/`.json` inside = 1 account |

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message & feature overview |
| `/help` | Supported format guide |
| Send file | Auto-detects format, checks, reports |
| Paste text | Detects cookie data, checks instantly |

## Country Detection

Country is resolved in priority order — never uses IP geolocation:
1. Cookie file metadata (combo / CookieCheckerPro format)
2. Account-specific JSON keys: `countryOfSignup`, `memberCountry`, `locale`
3. Netflix URL path redirect (`/fr/account` → 🇫🇷)
4. `OptanonConsent` cookie locale hint (`fr-FR` → 🇫🇷)

## Project Structure

```
scripts/netflix-bot/
├── bot.py          # Telegram bot — handlers, UI formatter, ZIP export
├── checker.py      # Cookie parsing, Netflix validation, NFToken generation
└── requirements.txt
Dockerfile
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ | Your Telegram bot token from @BotFather |
