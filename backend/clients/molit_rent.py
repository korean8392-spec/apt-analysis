"""국토교통부 아파트 전월세 실거래자료 API 클라이언트 (RTMSDataSvcAptRent).

실거래가(molit_trade.py)와 동일한 지역코드+계약년월 방식이며, 응답도 XML이다.
전세가율 계산에는 순수 전세(월세 0원)만 사용한다.
"""

from datetime import date
from xml.etree import ElementTree
import httpx

from config import MOLIT_SERVICE_KEY
import db

BASE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"


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
    cache_key = f"molit_rent:{lawd_cd}:{deal_ymd}"
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
        raise RuntimeError(f"MOLIT 전월세 API 응답 XML 파싱 실패: {resp.text[:300]}") from e

    result_code = root.findtext("header/resultCode")
    if result_code is not None and result_code != "000":
        result_msg = root.findtext("header/resultMsg")
        raise RuntimeError(f"MOLIT 전월세 API 오류({result_code}): {result_msg}")

    def text(item, tag):
        el = item.find(tag)
        return el.text if el is not None else None

    rows = []
    for item in root.findall("body/items/item"):
        monthly_rent = _clean_amount(text(item, "monthlyRent")) or 0
        rows.append(
            {
                "apt_name": (text(item, "aptNm") or "").strip(),
                "dong": (text(item, "umdNm") or "").strip(),
                "jibun": (text(item, "jibun") or "").strip(),
                "exclusive_area": _clean_float(text(item, "excluUseAr")),
                "floor": text(item, "floor"),
                "build_year": text(item, "buildYear"),
                "deposit_10k": _clean_amount(text(item, "deposit")),
                "monthly_rent_10k": monthly_rent,
                "is_jeonse": monthly_rent == 0,
                "deal_year": text(item, "dealYear"),
                "deal_month": text(item, "dealMonth"),
                "deal_day": text(item, "dealDay"),
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


async def get_rents(lawd_cd: str, months: int = 12) -> list[dict]:
    """최근 N개월 전월세 실거래 내역을 가져온다. 전세가율은 최근 1년이면 충분해 기본값을 12개월로 둔다."""
    all_rows: list[dict] = []
    for ymd in _last_n_months(months):
        rows = await _fetch_month(lawd_cd, ymd)
        all_rows.extend(rows)
    return all_rows
