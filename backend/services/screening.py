"""예산(가격 상한) 내 후보 단지 스크리닝.

시군구 + 예산으로 최근 1년 거래량 상위 단지를 추려낸 뒤, 각 후보의 최근 1년
가격 상승률(모멘텀)과 인근 지하철·학교 정보를 덧붙인다. "이 예산대에서
사용자에게 딱 맞는 추천"이 아니라 "조건에 맞는 후보를 데이터 기준으로 정렬해
보여주는" 스크리닝 도구다 — 개인 투자자문이 아니며, 최종 판단은 사용자 몫이다.
"""

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import mean

from clients import geocode as geocode_client
from clients import molit_trade
from clients import schools as schools_client
from clients import subway as subway_client
from services import dong_codes

ONE_YEAR_DAYS = 365
POOL_SIZE = 5


def _trade_date(row: dict) -> date | None:
    try:
        return date(int(row["deal_year"]), int(row["deal_month"]), int(row["deal_day"]))
    except (TypeError, ValueError, KeyError):
        return None


async def screen_by_budget(sigungu_keyword: str, max_price_10k: float, top_n: int = POOL_SIZE) -> dict:
    sigungu = dong_codes.resolve_sigungu(sigungu_keyword)
    trades = await molit_trade.get_trades(sigungu["code"], months=36)

    cutoff_1y = date.today() - timedelta(days=ONE_YEAR_DAYS)

    groups: dict[tuple[str, str], list[tuple[date, dict]]] = defaultdict(list)
    for t in trades:
        if t.get("cancel_deal"):
            continue
        price = t.get("deal_amount_10k")
        if price is None or price > max_price_10k:
            continue
        d = _trade_date(t)
        if not d or d < cutoff_1y:
            continue
        key = (t["apt_name"], t["dong"])
        groups[key].append((d, t))

    candidates = []
    for (name, dong), rows in groups.items():
        if len(rows) < 2:
            # 최근 1년 거래가 1건뿐이면 상승률(모멘텀)을 계산할 근거가 부족하다.
            continue
        rows.sort(key=lambda r: r[0])
        prices = [r[1]["deal_amount_10k"] for r in rows]
        mid = max(len(rows) // 2, 1)
        early_avg = mean(prices[:mid])
        late_avg = mean(prices[mid:]) if len(prices) > mid else prices[-1]
        momentum_pct = round((late_avg - early_avg) / early_avg * 100, 1) if early_avg else 0.0

        areas = [r[1]["exclusive_area"] for r in rows if r[1].get("exclusive_area")]
        avg_pyeong = round((sum(areas) / len(areas)) * 0.3025) if areas else None

        jibun_counts = Counter(r[1]["jibun"] for r in rows if r[1].get("jibun"))
        jibun = jibun_counts.most_common(1)[0][0] if jibun_counts else None

        candidates.append(
            {
                "apt_name": name,
                "dong": dong,
                "jibun": jibun,
                "trade_count_1y": len(rows),
                "avg_price_10k": round(mean(prices)),
                "avg_pyeong": avg_pyeong,
                "momentum_pct": momentum_pct,
            }
        )

    candidates.sort(key=lambda c: c["trade_count_1y"], reverse=True)
    top = candidates[:top_n]

    for c in top:
        geo = None
        if c["jibun"]:
            query = f"{sigungu['sido']} {sigungu['sigungu']} {c['dong']} {c['jibun']}"
            try:
                geo = await geocode_client.geocode(query)
            except Exception:
                geo = None
        c["subway_info"] = None
        c["school_info"] = None
        if geo:
            c["subway_info"] = await subway_client.find_nearest_station(geo["lat"], geo["lon"])
            c["school_info"] = await schools_client.find_nearby_schools(geo["lat"], geo["lon"])
        del c["jibun"]

    return {
        "sigungu": {"sido": sigungu["sido"], "sigungu": sigungu["sigungu"], "code": sigungu["code"]},
        "max_price_10k": max_price_10k,
        "total_matched_complexes": len(candidates),
        "candidates": top,
    }
