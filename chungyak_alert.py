"""청약 알림봇 — 수도권(서울/경기/인천) 새 청약 공고를 텔레그램으로 알려준다.

필요한 환경변수 3개:
    SERVICE_KEY      data.go.kr "한국부동산원_청약홈 분양정보 조회 서비스" 일반 인증키(Decoding)
    TELEGRAM_TOKEN   @BotFather 에게 받은 봇 토큰
    TELEGRAM_CHAT_ID 내 채팅 ID (콤마로 여러 명 지정 가능: "111,222")

동작:
    - APT 일반분양 + 무순위/잔여세대 + 오피스텔/도시형 3개 엔드포인트를 전부 수집
    - config.json 조건(지역/제외 키워드)에 맞는 공고만 필터
    - 이미 본 공고(seen.json)는 제외하고 새 공고만 텔레그램 푸시 (주택형별 분양가 포함)
    - 접수 시작 당일/전날 리마인더 전송
    - 첫 실행은 flood 방지를 위해 기록만 하고 요약 1건만 전송

설정(config.json — 없으면 기본값 사용, 코드 수정 없이 조정 가능):
    regions          알림 대상 지역 리스트 (SUBSCRPT_AREA_CODE_NM 기준)
    lookback_days    모집공고일 기준 최근 N일치만 조회
    max_detail_push  새 공고가 이보다 많으면 상세 대신 요약 전송
    max_price_lines  분양가 표시 최대 타입 수 (초과분은 가격 범위로 축약)
    exclude_keywords 주택명/주소에 이 단어가 있으면 무시 (예: ["도시형", "생활숙박"])
    reminders        {"today": true, "tomorrow": true} — 접수 시작 당일/전날 리마인더

seen.json 스키마 (v2 — 공고별 메타데이터, 리마인더 등 후속 기능의 토대):
    {"APT:2026000316": {"first_seen": "...", "name": "...", "region": "...",
                        "rcept_bgnde": "...", "rcept_endde": "...", "url": "...",
                        "reminded": ["tomorrow"]}}
    구버전(값이 날짜 문자열)은 로드 시 자동 마이그레이션.
"""

import os
import re
import sys
import json
import html
import datetime
import statistics
import xml.etree.ElementTree as ET
import requests

from lawd_codes import SIGUNGU_CODES

# ── 설정 ─────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(_DIR, "seen.json")
CONFIG_FILE = os.path.join(_DIR, "config.json")

DEFAULT_CONFIG = {
    "regions": ["서울", "경기", "인천"],
    "lookback_days": 90,
    "max_detail_push": 15,
    "max_price_lines": 6,
    "exclude_keywords": [],
    "reminders": {"today": True, "tomorrow": True, "announce": True},
    "cmpet_alert": True,
    "market_compare": True,
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            user = json.load(f)
    except FileNotFoundError:
        return cfg
    except ValueError as e:
        print(f"⚠️ config.json 파싱 실패, 기본값 사용: {e}", file=sys.stderr)
        return cfg
    for k, v in user.items():
        if isinstance(cfg.get(k), dict) and isinstance(v, dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


CFG = load_config()

BASE = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1"
ENDPOINTS = [
    ("APT",   "APT 일반분양",      f"{BASE}/getAPTLttotPblancDetail"),
    ("REMND", "무순위/잔여세대",   f"{BASE}/getRemndrLttotPblancDetail"),
    ("OFCTL", "오피스텔/도시형",   f"{BASE}/getUrbtyOfctlLttotPblancDetail"),
]

# 주택형별 상세(분양가) 엔드포인트 — 종류별로 필드가 조금 다름
MDL_URLS = {
    "APT":   f"{BASE}/getAPTLttotPblancMdl",
    "REMND": f"{BASE}/getRemndrLttotPblancMdl",
    "OFCTL": f"{BASE}/getUrbtyOfctlLttotPblancMdl",
}

# 경쟁률 엔드포인트 — 별도 서비스라 data.go.kr에서 추가 활용신청 필요
# ("한국부동산원_청약홈 청약접수 경쟁률 및 특별공급 신청현황 조회 서비스", data 15098905)
# 키가 미신청 상태(401)면 조용히 건너뛰고, 승인되면 자동으로 작동한다.
CMPET_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1"
CMPET_URLS = {
    "APT":   f"{CMPET_BASE}/getAPTLttotPblancCmpet",
    "REMND": f"{CMPET_BASE}/getRemndrLttotPblancCmpet",
    "OFCTL": f"{CMPET_BASE}/getUrbtyOfctlLttotPblancCmpet",
}
CMPET_WINDOW_DAYS = 14              # 접수 마감 후 N일까지만 경쟁률 발표를 기다린다

# 실거래가 (국토부) — 별도 서비스라 data.go.kr에서 추가 활용신청 필요
# ("국토교통부_아파트 매매 실거래가 자료"). 미신청 키면 시세 라인만 생략된다.
TRADE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
MARKET_MONTHS = 3                   # 최근 N개월 실거래와 비교
MARKET_AREA_TOL = 5.0               # 전용면적 ±N㎡를 같은 면적대로 취급
MARKET_MIN_TRADES = 3               # 거래가 이보다 적으면 표본 부족으로 표시 안 함

SERVICE_KEY = os.environ.get("SERVICE_KEY")
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_IDS = [s.strip() for s in (os.environ.get("TELEGRAM_CHAT_ID") or "").split(",") if s.strip()]


# ── 수집 ─────────────────────────────────────────────────
def fetch_all(url: str) -> list[dict]:
    """엔드포인트 하나를 page 순회하며 전부 가져온다."""
    since = (datetime.date.today() - datetime.timedelta(days=CFG["lookback_days"])).isoformat()
    items, page = [], 1
    while True:
        r = requests.get(url, params={
            "serviceKey": SERVICE_KEY,
            "page": page,
            "perPage": 100,
            "cond[RCRIT_PBLANC_DE::GTE]": since,
        }, timeout=30)
        r.raise_for_status()
        body = r.json()
        data = body.get("data", [])
        items.extend(data)
        total = body.get("matchCount", body.get("totalCount", 0))
        if not data or len(items) >= total:
            return items
        page += 1


def wanted(row: dict) -> bool:
    """지역/키워드 필터."""
    region = (row.get("SUBSCRPT_AREA_CODE_NM") or "").strip()
    if region not in set(CFG["regions"]):
        return False
    text = f"{row.get('HOUSE_NM') or ''} {row.get('HSSPLY_ADRES') or ''}"
    return not any(kw in text for kw in CFG["exclude_keywords"])


def collect() -> list[dict]:
    """3개 엔드포인트를 수집해 조건에 맞는 공고만 (type 정보를 붙여서) 반환."""
    result = []
    for type_code, type_name, url in ENDPOINTS:
        try:
            rows = fetch_all(url)
        except Exception as e:
            print(f"⚠️ {type_name} 수집 실패: {e}", file=sys.stderr)
            continue
        for row in rows:
            if not wanted(row):
                continue
            row["_TYPE_CODE"] = type_code
            row["_TYPE_NAME"] = type_name
            result.append(row)
    return result


def item_id(row: dict) -> str:
    """중복 판별키. 엔드포인트 간 번호 충돌 대비로 종류 코드를 붙인다."""
    return f"{row['_TYPE_CODE']}:{row.get('HOUSE_MANAGE_NO') or row.get('PBLANC_NO')}"


def rcept_dates(row: dict) -> tuple[str | None, str | None]:
    """접수 시작/종료일. 엔드포인트마다 필드명이 다르다:
    APT=RCEPT_*, 무순위/오피스텔=SUBSCRPT_RCEPT_* (무순위는 GNRL_RCEPT_*도)."""
    begin = row.get("RCEPT_BGNDE") or row.get("SUBSCRPT_RCEPT_BGNDE") or row.get("GNRL_RCEPT_BGNDE")
    end = row.get("RCEPT_ENDDE") or row.get("SUBSCRPT_RCEPT_ENDDE") or row.get("GNRL_RCEPT_ENDDE")
    return begin, end


def item_meta(row: dict) -> dict:
    """seen.json에 저장할 공고 메타데이터 (리마인더 등 후속 기능이 소비)."""
    begin, end = rcept_dates(row)
    return {
        "name": row.get("HOUSE_NM"),
        "region": (row.get("SUBSCRPT_AREA_CODE_NM") or "").strip(),
        "type": row["_TYPE_NAME"],
        "rcept_bgnde": begin,
        "rcept_endde": end,
        "przwner_de": row.get("PRZWNER_PRESNATN_DE"),
        "url": row.get("PBLANC_URL"),
    }


# ── seen.json ────────────────────────────────────────────
def load_seen() -> dict:
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            seen = json.load(f)
    except FileNotFoundError:
        return {}
    # v1(값이 날짜 문자열) → v2(dict) 마이그레이션
    return {k: (v if isinstance(v, dict) else {"first_seen": v}) for k, v in seen.items()}


def save_seen(seen: dict) -> None:
    # LOOKBACK 범위를 벗어난 옛 기록은 정리 (파일 무한 성장 방지)
    cutoff = (datetime.date.today() - datetime.timedelta(days=CFG["lookback_days"] * 2)).isoformat()
    seen = {k: v for k, v in seen.items() if v.get("first_seen", "") >= cutoff}
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=1)


# ── 분양가 ───────────────────────────────────────────────
def _fmt_house_type(m: dict) -> str:
    """'084.9796A' → '84A', 오피스텔 'TP' 값은 그대로."""
    raw = (m.get("HOUSE_TY") or m.get("TP") or "").strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([A-Za-z]*)", raw)
    if match:
        return f"{int(float(match.group(1)))}{match.group(2)}"
    return raw or "?"


def _fmt_amount(man_won: int) -> str:
    """만원 단위 금액 → '7.8억' / '9,500만'."""
    if man_won >= 10000:
        eok = man_won / 10000
        return f"{eok:.1f}억".replace(".0억", "억")
    return f"{man_won:,}만"


def fetch_price_entries(row: dict) -> list[tuple[str, int, int]]:
    """주택형별 (타입, 분양가 만원, 세대수) 목록. 실패하면 빈 리스트."""
    url = MDL_URLS.get(row["_TYPE_CODE"])
    manage_no = row.get("HOUSE_MANAGE_NO")
    if not url or not manage_no:
        return []
    try:
        r = requests.get(url, params={
            "serviceKey": SERVICE_KEY,
            "page": 1,
            "perPage": 50,
            "cond[HOUSE_MANAGE_NO::EQ]": manage_no,
        }, timeout=30)
        r.raise_for_status()
        models = r.json().get("data", [])
    except Exception as e:
        print(f"⚠️ 주택형 조회 실패({manage_no}): {e}", file=sys.stderr)
        return []

    entries = []
    for m in models:
        amt = m.get("LTTOT_TOP_AMOUNT") or m.get("SUPLY_AMOUNT")
        try:
            amt = int(str(amt).replace(",", ""))
        except (TypeError, ValueError):
            continue
        hshld = (m.get("SUPLY_HSHLDCO") or 0) + (m.get("SPSPLY_HSHLDCO") or 0)
        entries.append((_fmt_house_type(m), amt, hshld))
    return entries


def price_lines(entries: list[tuple[str, int, int]]) -> list[str]:
    """주택형별 분양가(최고가 기준) 메시지 라인."""
    if not entries:
        return []
    cap = CFG["max_price_lines"]
    lines = ["💰 분양가 (최고가 기준)"]
    for ty, amt, hshld in entries[:cap]:
        line = f" · {ty}㎡ {_fmt_amount(amt)}"
        if hshld:
            line += f" ({hshld}세대)"
        lines.append(line)
    if len(entries) > cap:
        rest = entries[cap:]
        lo, hi = min(a for _, a, _ in rest), max(a for _, a, _ in rest)
        price = _fmt_amount(lo) if lo == hi else f"{_fmt_amount(lo)}~{_fmt_amount(hi)}"
        lines.append(f" · 외 {len(rest)}개 타입 {price}")
    return lines


# ── 실거래가 시세 비교 ───────────────────────────────────
_trade_cache: dict = {}   # 시군구코드 → 거래 목록 (실행 1회 내 캐시), None = 미신청 키


def sigungu_for(addr: str | None) -> tuple[str, str] | None:
    """공급위치 주소에서 (시군구명, 법정동코드)를 찾는다. 가장 긴 접두사 우선."""
    if not addr:
        return None
    best = None
    for name, code in SIGUNGU_CODES.items():
        if addr.startswith(name) and (best is None or len(name) > len(best[0])):
            best = (name, code)
    return best


def fetch_trades(lawd_cd: str) -> list[tuple[float, int]] | None:
    """시군구의 최근 MARKET_MONTHS개월 아파트 매매 (전용면적㎡, 거래금액 만원).
    미신청 키(401 등)면 None."""
    if lawd_cd in _trade_cache:
        return _trade_cache[lawd_cd]
    trades = []
    today = datetime.date.today()
    for i in range(MARKET_MONTHS):
        y, m = today.year, today.month - i
        if m <= 0:
            y, m = y - 1, m + 12
        r = requests.get(TRADE_URL, params={
            "serviceKey": SERVICE_KEY,
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": f"{y}{m:02d}",
            "pageNo": 1,
            "numOfRows": 2000,
        }, timeout=30)
        if r.status_code in (401, 403) or "SERVICE_KEY" in r.text[:300] or r.text.strip() == "Unauthorized":
            _trade_cache[lawd_cd] = None
            print("⚠️ 실거래가 API 미신청 키 — data.go.kr에서 "
                  "'국토교통부_아파트 매매 실거래가 자료' 활용신청 필요", file=sys.stderr)
            return None
        r.raise_for_status()
        for it in ET.fromstring(r.text).iter("item"):
            try:
                area = float((it.findtext("excluUseAr") or "").strip())
                amt = int((it.findtext("dealAmount") or "").replace(",", "").strip())
            except ValueError:
                continue
            trades.append((area, amt))
    _trade_cache[lawd_cd] = trades
    return trades


def market_lines(row: dict, entries: list[tuple[str, int, int]]) -> list[str]:
    """분양가를 같은 시군구 최근 실거래 중위가와 면적대별로 비교한 라인.
    오피스텔은 아파트 실거래와 비교가 무의미해서 제외."""
    if not CFG.get("market_compare", True) or row["_TYPE_CODE"] == "OFCTL" or not entries:
        return []
    loc = sigungu_for(row.get("HSSPLY_ADRES"))
    if not loc:
        return []
    sigungu_name, lawd_cd = loc
    try:
        trades = fetch_trades(lawd_cd)
    except Exception as e:
        print(f"⚠️ 실거래 조회 실패({sigungu_name}): {e}", file=sys.stderr)
        return []
    if not trades:
        return []

    # 분양 타입을 면적대(정수 ㎡)로 묶고 면적대별 분양가 범위를 계산
    groups: dict[int, list[int]] = {}
    for ty, amt, _ in entries:
        num = re.match(r"\d+", ty)
        if num:
            groups.setdefault(int(num.group()), []).append(amt)

    short_name = sigungu_name.split(" ", 1)[1]   # "경기도 오산시" → "오산시"
    lines = []
    for area, amts in sorted(groups.items())[:3]:
        near = [amt for a, amt in trades if abs(a - area) <= MARKET_AREA_TOL]
        if len(near) < MARKET_MIN_TRADES:
            continue
        median = int(statistics.median(near))
        lo, hi = min(amts), max(amts)
        supply = _fmt_amount(lo) if lo == hi else f"{_fmt_amount(lo)}~{_fmt_amount(hi)}"
        diff = (lo + hi) // 2 - median
        verdict = f"{_fmt_amount(abs(diff))} {'비쌈' if diff > 0 else '저렴'}" if abs(diff) >= 1000 else "비슷"
        lines.append(f" · {area}㎡대: 실거래 중위 {_fmt_amount(median)} ({len(near)}건) vs 분양가 {supply} → {verdict}")
    if not lines:
        return []
    return [f"📈 시세 비교 ({short_name} 최근 {MARKET_MONTHS}개월)"] + lines


# ── 메시지 ───────────────────────────────────────────────
def format_item(row: dict) -> str:
    name = html.escape(row.get("HOUSE_NM") or "(이름 없음)")
    lines = [f"🏠 <b>{name}</b>  [{row['_TYPE_NAME']}]"]

    region = row.get("SUBSCRPT_AREA_CODE_NM") or "?"
    addr = row.get("HSSPLY_ADRES")
    lines.append(f"📍 {region}" + (f" · {html.escape(addr)}" if addr else ""))

    supply = row.get("TOT_SUPLY_HSHLDCO")
    if supply:
        lines.append(f"🏘️ 공급 {supply}세대")

    entries = fetch_price_entries(row)
    lines.extend(price_lines(entries))
    lines.extend(market_lines(row, entries))

    begin, end = rcept_dates(row)
    if begin or end:
        lines.append(f"🗓️ 접수 {begin or '?'} ~ {end or '?'}")
    notice = row.get("RCRIT_PBLANC_DE")
    if notice:
        lines.append(f"📢 공고일 {notice}")

    url = row.get("PBLANC_URL")
    if url:
        lines.append(f'🔗 <a href="{url}">공고 보기</a>')
    return "\n".join(lines)


def _days_until(date_str: str | None, today: datetime.date) -> int | None:
    if not date_str:
        return None
    try:
        return (datetime.date.fromisoformat(date_str) - today).days
    except ValueError:
        return None


def build_reminder(seen: dict) -> str | None:
    """접수 시작(오늘/내일)·당첨자 발표(오늘) 리마인더 메시지. 보낸 항목은 reminded에 기록."""
    today = datetime.date.today()
    buckets = {"today": [], "tomorrow": [], "announce": []}
    for meta in seen.values():
        reminded = meta.setdefault("reminded", [])

        # 접수 시작 D-1 / D-day
        flag = {0: "today", 1: "tomorrow"}.get(_days_until(meta.get("rcept_bgnde"), today))
        if flag and CFG["reminders"].get(flag, True) and flag not in reminded:
            reminded.append(flag)
            buckets[flag].append(meta)

        # 당첨자 발표 당일
        if (_days_until(meta.get("przwner_de"), today) == 0
                and CFG["reminders"].get("announce", True) and "announce" not in reminded):
            reminded.append("announce")
            buckets["announce"].append(meta)

    if not any(buckets.values()):
        return None

    lines = ["⏰ <b>청약 일정 알림</b>"]
    labels = [("today", "오늘 접수 시작"), ("tomorrow", "내일 접수 시작"), ("announce", "🎉 오늘 당첨자 발표")]
    for flag, label in labels:
        if not buckets[flag]:
            continue
        lines.append(f"[{label}]")
        for m in buckets[flag]:
            name = html.escape(m.get("name") or "?")
            if m.get("url"):
                name = f'<a href="{m["url"]}">{name}</a>'
            endde = f" (~{m['rcept_endde']})" if flag != "announce" and m.get("rcept_endde") else ""
            lines.append(f" · {name} ({m.get('region', '?')}){endde}")
    return "\n".join(lines)


def build_cmpet_alerts(seen: dict) -> list[str]:
    """접수가 끝난 공고의 경쟁률이 발표되면 알림 메시지를 만든다.
    발표 전이면 다음 실행에 재시도, 마감 CMPET_WINDOW_DAYS일 경과 시 포기.
    경쟁률 API 미신청 키(401)면 경고만 남기고 전체 건너뜀."""
    if not CFG.get("cmpet_alert", True):
        return []
    today = datetime.date.today()
    msgs = []
    for key, meta in seen.items():
        reminded = meta.setdefault("reminded", [])
        endde = meta.get("rcept_endde")
        if "cmpet" in reminded or not endde:
            continue
        try:
            days_after = (today - datetime.date.fromisoformat(endde)).days
        except ValueError:
            continue
        if not (1 <= days_after <= CMPET_WINDOW_DAYS):
            continue
        type_code, _, manage_no = key.partition(":")
        url = CMPET_URLS.get(type_code)
        if not url or not manage_no:
            continue
        try:
            r = requests.get(url, params={
                "serviceKey": SERVICE_KEY,
                "page": 1,
                "perPage": 100,
                "cond[HOUSE_MANAGE_NO::EQ]": manage_no,
            }, timeout=30)
            if r.status_code in (401, 403):
                print("⚠️ 경쟁률 API 미신청 키 — data.go.kr에서 "
                      "'청약홈 청약접수 경쟁률…' 서비스 활용신청 필요", file=sys.stderr)
                return msgs
            r.raise_for_status()
            rows = r.json().get("data", [])
        except Exception as e:
            print(f"⚠️ 경쟁률 조회 실패({manage_no}): {e}", file=sys.stderr)
            continue

        msg = format_cmpet(meta, rows)
        if msg:
            reminded.append("cmpet")
            msgs.append(msg)
    return msgs


def format_cmpet(meta: dict, rows: list[dict]) -> str | None:
    """경쟁률 행들을 메시지로. 아직 수치가 없으면 None (다음 실행에 재시도).

    행이 (주택형 × 순위 × 해당/기타지역) 조합으로 오므로, 타입당 대표값 1줄만 표시:
    1순위·해당지역 우선, 없으면 차선(1순위 기타 → 2순위 순)."""
    best = {}   # 타입 → (우선순위점수, rate, rank, reside)
    for r in rows:
        rate = str(r.get("CMPET_RATE") or "").strip()
        if not rate or rate == "-":
            continue
        ty = _fmt_house_type(r)
        rank = str(r.get("SUBSCRPT_RANK_CODE") or "").strip()
        reside = str(r.get("RESIDE_SENM") or "").strip()
        score = (0 if rank in ("1", "1순위") else 1, 0 if "해당" in reside else 1)
        if ty not in best or score < best[ty][0]:
            best[ty] = (score, rate, rank, reside)
    if not best:
        return None

    name = html.escape(meta.get("name") or "?")
    lines = [f"📊 <b>경쟁률 발표</b> — {name} ({meta.get('region', '?')})",
             "(1순위 해당지역 기준)"]
    for ty, (score, rate, rank, reside) in list(best.items())[:12]:
        # 숫자면 "58:1"/"4.53:1" 형태로 (불필요한 0 제거), 미달(△N) 같은 표기는 그대로
        try:
            disp = f"{float(rate.replace(',', '')):g}:1"
        except ValueError:
            disp = rate
        note = ""
        if score != (0, 0):  # 대표값이 1순위·해당지역이 아니면 출처를 명시
            note = f" ({rank}순위{'·' + reside if reside and '해당' not in reside else ''})"
        lines.append(f" · {ty}㎡ {disp}{note}")
    if len(best) > 12:
        lines.append(f" · … 외 {len(best) - 12}개 타입")
    if meta.get("url"):
        lines.append(f'🔗 <a href="{meta["url"]}">공고 보기</a>')
    return "\n".join(lines)


def send_telegram(text: str) -> None:
    if not TOKEN or not CHAT_IDS:
        print("❌ TELEGRAM_TOKEN / TELEGRAM_CHAT_ID 환경변수가 없어요.", file=sys.stderr)
        print("아래는 보내려던 메시지입니다:\n", file=sys.stderr)
        print(text)
        sys.exit(1)

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    ok = 0
    for chat_id in CHAT_IDS:
        resp = requests.post(url, data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        if resp.status_code == 200:
            ok += 1
        else:
            # 수신자 하나가 실패해도 (예: 아직 봇에 /start 안 함) 나머지에겐 보낸다
            print(f"⚠️ 텔레그램 전송 실패({chat_id}): {resp.status_code} {resp.text}", file=sys.stderr)
    if ok == 0:
        print("❌ 모든 수신자 전송 실패.", file=sys.stderr)
        sys.exit(1)


# ── 메인 ─────────────────────────────────────────────────
def main() -> None:
    if not SERVICE_KEY:
        print("❌ SERVICE_KEY 환경변수가 없어요. (data.go.kr 일반 인증키 Decoding)", file=sys.stderr)
        sys.exit(1)

    items = collect()
    if not items:
        print("⚠️ 수집된 공고가 0건입니다. API 키/네트워크를 확인하세요.", file=sys.stderr)
        sys.exit(1)

    seen = load_seen()
    first_run = not seen
    today = datetime.date.today().isoformat()

    # 새 공고 기록 + 기존 공고 메타데이터 최신화 (구 스키마 백필 포함)
    new_items, closed_skipped = [], 0
    for row in items:
        key = item_id(row)
        if key in seen:
            first_seen = seen[key].get("first_seen", today)
            reminded = seen[key].get("reminded", [])
            seen[key] = {"first_seen": first_seen, "reminded": reminded, **item_meta(row)}
        else:
            seen[key] = {"first_seen": today, "reminded": [], **item_meta(row)}
            # 접수가 이미 끝난 공고는 청약할 수 없으므로 기록만 하고 알림은 생략
            # (발표일·경쟁률 후속 알림은 메타데이터 기반으로 계속 추적됨)
            _, endde = rcept_dates(row)
            if endde and endde < today:
                closed_skipped += 1
            else:
                new_items.append(row)

    if first_run:
        # flood 방지: 기록만 하고 요약 전송
        save_seen(seen)
        counts = {}
        for row in items:
            counts[row["_TYPE_NAME"]] = counts.get(row["_TYPE_NAME"], 0) + 1
        detail = " · ".join(f"{k} {v}건" for k, v in counts.items())
        send_telegram(
            f"👀 <b>청약 감시 시작</b>\n"
            f"{'/'.join(CFG['regions'])} 최근 {CFG['lookback_days']}일 공고 {len(items)}건을 기억했어요.\n"
            f"({detail})\n이제부터 새 공고가 뜨면 알려드릴게요."
        )
        print(f"✅ 첫 실행: {len(items)}건 기록, 요약 전송 완료.")
        return

    # 새 공고 알림
    if new_items:
        if len(new_items) <= CFG["max_detail_push"]:
            for row in new_items:
                send_telegram(format_item(row))
        else:
            names = "\n".join(
                f"· {row.get('HOUSE_NM')} ({row.get('SUBSCRPT_AREA_CODE_NM')}, {row['_TYPE_NAME']})"
                for row in new_items[:30]
            )
            send_telegram(
                f"🔔 <b>새 청약 공고 {len(new_items)}건</b>\n{html.escape(names)}\n"
                f"자세한 내용은 청약홈에서 확인하세요."
            )

    # 접수 임박·발표일 리마인더
    reminder = build_reminder(seen)
    if reminder:
        send_telegram(reminder)

    # 경쟁률 발표 알림
    cmpet_msgs = build_cmpet_alerts(seen)
    for msg in cmpet_msgs:
        send_telegram(msg)

    save_seen(seen)
    skipped = f" (접수 종료된 공고 {closed_skipped}건은 기록만)" if closed_skipped else ""
    summary = (f"✅ 새 공고 {len(new_items)}건, 리마인더 {'1건' if reminder else '없음'}, "
               f"경쟁률 {len(cmpet_msgs)}건 — 완료.{skipped}")
    print(summary)
    # heartbeat: 아무 메시지도 안 나간 실행이면 한 줄 전송 — "안 옴 = 미실행"으로 구분 가능
    if not (new_items or reminder or cmpet_msgs):
        send_telegram("😴 새로운 공고 없음 — 오늘도 잘 지켜보고 있어요.")


if __name__ == "__main__":
    main()
