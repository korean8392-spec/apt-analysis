"""가격대 + 시군구 기준 환금성(거래량) 순위.

"환금성이 좋다"를 거래량(최근 1년 실거래 건수)으로 근사한다 — 거래가 잦을수록
사고팔기 쉬운 단지로 본다. 이미 시군구 단위로 받아오는 실거래가 데이터를
그대로 재활용하므로 추가 API 호출이 필요 없다.
"""

from collections import defaultdict
from datetime import date, timedelta

from clients import molit_trade
from services import dong_codes

PERIOD_MONTHS = 12


def _trade_date(row: dict) -> date | None:
    try:
        return date(int(row["deal_year"]), int(row["deal_month"]), int(row["deal_day"]))
    except (TypeError, ValueError, KeyError):
        return None


async def rank_liquidity(
    sigungu_keyword: str,
    price_min_10k: float | None = None,
    price_max_10k: float | None = None,
    top_n: int = 10,
) -> dict:
    sigungu = dong_codes.resolve_sigungu(sigungu_keyword)
    # 순위는 최근 PERIOD_MONTHS(12개월) 거래만 쓰므로 그만큼(+현재 달)만 받는다. 예전에는 36개월을
    # 받아 필요한 것의 3배를 호출했다. 월별 캐시는 다른 기능과 공유되어 중복 호출도 없다.
    # 국토부 응답이 불안정할 때 일부 달이 빠져도 받은 만큼 보여주고, 빠진 달은 화면에 알린다.
    trades, missing_months, first_error = await molit_trade.get_trades_partial(
        sigungu["code"], months=PERIOD_MONTHS + 1
    )
    if not trades and first_error is not None:
        raise first_error

    cutoff = date.today() - timedelta(days=PERIOD_MONTHS * 30)

    # 같은 이름의 단지가 다른 법정동에도 있을 수 있어(실제로 발견됨, "주공12" 사례)
    # 단지명만이 아니라 (단지명, 동)으로 묶어야 서로 다른 단지가 뭉치지 않는다.
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in trades:
        if t.get("cancel_deal"):
            continue
        price = t.get("deal_amount_10k")
        if price is None:
            continue
        if price_min_10k is not None and price < price_min_10k:
            continue
        if price_max_10k is not None and price > price_max_10k:
            continue
        d = _trade_date(t)
        if not d or d < cutoff:
            continue
        key = (t["apt_name"], t["dong"])
        groups[key].append(t)

    ranking = []
    for (name, dong), rows in groups.items():
        prices = [r["deal_amount_10k"] for r in rows]
        areas = [r["exclusive_area"] for r in rows if r.get("exclusive_area")]
        avg_area = sum(areas) / len(areas) if areas else None
        ranking.append(
            {
                "apt_name": name,
                "dong": dong,
                "trade_count": len(rows),
                "avg_price_10k": round(sum(prices) / len(prices)),
                "min_price_10k": min(prices),
                "max_price_10k": max(prices),
                "avg_pyeong": round(avg_area * 0.3025) if avg_area else None,
            }
        )
    ranking.sort(key=lambda r: r["trade_count"], reverse=True)

    return {
        "sigungu": {"sido": sigungu["sido"], "sigungu": sigungu["sigungu"], "code": sigungu["code"]},
        "period_months": PERIOD_MONTHS,
        "price_min_10k": price_min_10k,
        "price_max_10k": price_max_10k,
        "ranking": ranking[:top_n],
        "total_matched_complexes": len(ranking),
        "missing_months": missing_months,
    }
