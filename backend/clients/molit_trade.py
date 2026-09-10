"""국토교통부 아파트매매 실거래자료 API 클라이언트.

공공데이터포털 "국토교통부_아파트매매 실거래자료" (RTMSDataSvcAptTrade).
지역코드(법정동코드 앞 5자리) + 계약년월(YYYYMM) 단위로 조회한다.
이 API는 type=json을 줘도 무시하고 항상 XML을 반환하는 것이 실제 호출로 확인되어
XML로 파싱한다.
"""

from datetime import date
from xml.etree import ElementTree
import httpx

from config import MOLIT_SERVICE_KEY
import db

BASE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"


def _clean_amount(raw: str | None) -> int | None:
    if raw is None:
        return None
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _clean_float(raw) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        return None


async def _fetch_month(lawd_cd: str, deal_ymd: str) -> list[dict]:
    cache_key = f"molit_trade:{lawd_cd}:{deal_ymd}"
    # 최근 2개월은 정정신고/해제신고로 값이 바뀔 수 있어 캐시를 짧게, 그 이전은 길게 유지.
    today = date.today()
    recent = (today.year - int(deal_ymd[:4])) * 12 + (today.month - int(deal_ymd[4:6])) <= 2
    max_age = 60 * 60 * 6 if recent else 60 * 60 * 24 * 30

    cached = db.cache_get(cache_key, max_age)
    if cached is not None:
        return cached

    params = {
        "serviceKey": MOLIT_SERVICE_KEY,
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": deal_ymd,
        "numOfRows": "1000",
        "pageNo": "1",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(BASE_URL, params=params)
        resp.raise_for_status()

    try:
        root = ElementTree.fromstring(resp.text)
    except ElementTree.ParseError as e:
        raise RuntimeError(f"MOLIT 실거래가 API 응답 XML 파싱 실패: {resp.text[:300]}") from e

    result_code = root.findtext("header/resultCode")
    if result_code is not None and result_code != "000":
        result_msg = root.findtext("header/resultMsg")
        raise RuntimeError(f"MOLIT 실거래가 API 오류({result_code}): {result_msg}")

    def text(item, tag):
        el = item.find(tag)
        return el.text if el is not None else None

    rows = []
    for item in root.findall("body/items/item"):
        rows.append(
            {
                "apt_name": (text(item, "aptNm") or "").strip(),
                "dong": (text(item, "umdNm") or "").strip(),
                "jibun": (text(item, "jibun") or "").strip(),
                "exclusive_area": _clean_float(text(item, "excluUseAr")),
                "floor": text(item, "floor"),
                "build_year": text(item, "buildYear"),
                "deal_amount_10k": _clean_amount(text(item, "dealAmount")),
                "deal_year": text(item, "dealYear"),
                "deal_month": text(item, "dealMonth"),
                "deal_day": text(item, "dealDay"),
                "cancel_deal": (text(item, "cdealType") or "").strip() == "O",
            }
        )
    db.cache_set(cache_key, rows)
    return rows


def _last_n_months(n: int) -> list[str]:
    months = []
    y, m = date.today().year, date.today().month
    for _ in range(n):
        months.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return months


async def get_trades(lawd_cd: str, months: int = 36) -> list[dict]:
    """최근 N개월 실거래가 내역을 모두 가져온다 (해제신고 건 포함, cancel_deal 플래그로 표시)."""
    all_rows: list[dict] = []
    for ymd in _last_n_months(months):
        rows = await _fetch_month(lawd_cd, ymd)
        all_rows.extend(rows)
    return all_rows
