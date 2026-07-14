"""청약 알림봇 — 수도권(서울/경기/인천) 새 청약 공고를 텔레그램으로 알려준다.

필요한 환경변수 3개:
    SERVICE_KEY      data.go.kr "한국부동산원_청약홈 분양정보 조회 서비스" 일반 인증키(Decoding)
    TELEGRAM_TOKEN   @BotFather 에게 받은 봇 토큰
    TELEGRAM_CHAT_ID 내 채팅 ID

동작:
    - APT 일반분양 + 무순위/잔여세대 + 오피스텔/도시형 3개 엔드포인트를 전부 수집
    - 서울/경기/인천 공고만 필터
    - 이미 본 공고(seen.json)는 제외하고 새 공고만 텔레그램 푸시
    - 첫 실행은 flood 방지를 위해 기록만 하고 요약 1건만 전송
"""

import os
import re
import sys
import json
import html
import datetime
import requests

# ── 설정 ─────────────────────────────────────────────────
REGIONS = {"서울", "경기", "인천"}   # SUBSCRPT_AREA_CODE_NM 이 이 중 하나면 알림
LOOKBACK_DAYS = 90                  # 모집공고일 기준 최근 N일치만 조회 (트래픽 절약)
MAX_DETAIL_PUSH = 15                # 새 공고가 이보다 많으면 상세 대신 요약 전송
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")

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
MAX_PRICE_LINES = 6                 # 주택형이 이보다 많으면 나머지는 "외 N개 타입"으로 축약

SERVICE_KEY = os.environ.get("SERVICE_KEY")
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ── 수집 ─────────────────────────────────────────────────
def fetch_all(url: str) -> list[dict]:
    """엔드포인트 하나를 page 순회하며 전부 가져온다."""
    since = (datetime.date.today() - datetime.timedelta(days=LOOKBACK_DAYS)).isoformat()
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


def collect() -> list[dict]:
    """3개 엔드포인트를 수집해 수도권 공고만 (type 정보를 붙여서) 반환."""
    result = []
    for type_code, type_name, url in ENDPOINTS:
        try:
            rows = fetch_all(url)
        except Exception as e:
            print(f"⚠️ {type_name} 수집 실패: {e}", file=sys.stderr)
            continue
        for row in rows:
            region = (row.get("SUBSCRPT_AREA_CODE_NM") or "").strip()
            if region not in REGIONS:
                continue
            row["_TYPE_CODE"] = type_code
            row["_TYPE_NAME"] = type_name
            result.append(row)
    return result


def item_id(row: dict) -> str:
    """중복 판별키. 엔드포인트 간 번호 충돌 대비로 종류 코드를 붙인다."""
    return f"{row['_TYPE_CODE']}:{row.get('HOUSE_MANAGE_NO') or row.get('PBLANC_NO')}"


# ── seen.json ────────────────────────────────────────────
def load_seen() -> dict:
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_seen(seen: dict) -> None:
    # LOOKBACK 범위를 벗어난 옛 기록은 정리 (파일 무한 성장 방지)
    cutoff = (datetime.date.today() - datetime.timedelta(days=LOOKBACK_DAYS * 2)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=1)


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


def fetch_price_lines(row: dict) -> list[str]:
    """주택형별 분양가(최고가 기준) 메시지 라인을 만든다. 실패하면 빈 리스트."""
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
    if not entries:
        return []

    lines = ["💰 분양가 (최고가 기준)"]
    for ty, amt, hshld in entries[:MAX_PRICE_LINES]:
        line = f" · {ty}㎡ {_fmt_amount(amt)}"
        if hshld:
            line += f" ({hshld}세대)"
        lines.append(line)
    if len(entries) > MAX_PRICE_LINES:
        rest = entries[MAX_PRICE_LINES:]
        lo, hi = min(a for _, a, _ in rest), max(a for _, a, _ in rest)
        price = _fmt_amount(lo) if lo == hi else f"{_fmt_amount(lo)}~{_fmt_amount(hi)}"
        lines.append(f" · 외 {len(rest)}개 타입 {price}")
    return lines


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

    lines.extend(fetch_price_lines(row))

    begin, end = row.get("RCEPT_BGNDE"), row.get("RCEPT_ENDDE")
    if begin or end:
        lines.append(f"🗓️ 접수 {begin or '?'} ~ {end or '?'}")
    notice = row.get("RCRIT_PBLANC_DE")
    if notice:
        lines.append(f"📢 공고일 {notice}")

    url = row.get("PBLANC_URL")
    if url:
        lines.append(f'🔗 <a href="{url}">공고 보기</a>')
    return "\n".join(lines)


def send_telegram(text: str) -> None:
    if not TOKEN or not CHAT_ID:
        print("❌ TELEGRAM_TOKEN / TELEGRAM_CHAT_ID 환경변수가 없어요.", file=sys.stderr)
        print("아래는 보내려던 메시지입니다:\n", file=sys.stderr)
        print(text)
        sys.exit(1)

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    resp = requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=15)
    if resp.status_code != 200:
        print(f"❌ 텔레그램 전송 실패: {resp.status_code} {resp.text}", file=sys.stderr)
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

    new_items = [row for row in items if item_id(row) not in seen]
    for row in new_items:
        seen[item_id(row)] = today

    if first_run:
        # flood 방지: 기록만 하고 요약 전송
        save_seen(seen)
        counts = {}
        for row in items:
            counts[row["_TYPE_NAME"]] = counts.get(row["_TYPE_NAME"], 0) + 1
        detail = " · ".join(f"{k} {v}건" for k, v in counts.items())
        send_telegram(
            f"👀 <b>청약 감시 시작</b>\n수도권 최근 {LOOKBACK_DAYS}일 공고 {len(items)}건을 기억했어요.\n"
            f"({detail})\n이제부터 새 공고가 뜨면 알려드릴게요."
        )
        print(f"✅ 첫 실행: {len(items)}건 기록, 요약 전송 완료.")
        return

    if not new_items:
        save_seen(seen)
        print("새 공고 없음.")
        return

    if len(new_items) <= MAX_DETAIL_PUSH:
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
    save_seen(seen)
    print(f"✅ 새 공고 {len(new_items)}건 알림 완료.")


if __name__ == "__main__":
    main()
