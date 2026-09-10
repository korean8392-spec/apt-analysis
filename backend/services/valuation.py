"""실거래가 이력을 기반으로 평형별 적정가격을 추정한다.

방법론(단순하지만 설명 가능한 방식을 택함):
1. 전용면적을 평형 그룹으로 클러스터링(±3㎡ 이내는 같은 평형으로 간주).
2. 그룹별로 최근 3년 실거래가에 "최근일수록 더 큰 가중치"(반감기 180일 지수감쇠)를
   부여한 가중회귀직선을 적합하여 오늘 시점의 추세 추정가를 계산.
3. 표본이 3건 미만이면 회귀 대신 가중평균으로 대체.
4. 직전거래가(가장 최근 거래)를 함께 표시해 추세 추정가와 비교할 수 있게 함.
5. 표본 수에 따라 신뢰도(high/medium/low)를 표시 — 이 추정치는 통계적 참고용이며
   투자 자문이 아니다.
"""

import math
from datetime import date, timedelta

HALF_LIFE_DAYS = 180
DECAY_LAMBDA = math.log(2) / HALF_LIFE_DAYS


def _parse_deal_date(row: dict) -> date | None:
    try:
        y = int(row["deal_year"])
        m = int(row["deal_month"])
        d = int(row["deal_day"])
        return date(y, m, d)
    except (TypeError, ValueError, KeyError):
        return None


def cluster_by_pyeong(trades: list[dict], tolerance: float = 3.0) -> list[dict]:
    """exclusive_area 기준으로 평형 그룹을 만든다."""
    valid = [t for t in trades if t.get("exclusive_area") and not t.get("cancel_deal")]
    valid.sort(key=lambda t: t["exclusive_area"])

    groups: list[list[dict]] = []
    for t in valid:
        if groups and t["exclusive_area"] - groups[-1][-1]["exclusive_area"] <= tolerance:
            groups[-1].append(t)
        else:
            groups.append([t])

    result = []
    for g in groups:
        areas = [t["exclusive_area"] for t in g]
        avg_area = sum(areas) / len(areas)
        pyeong = round(avg_area * 0.3025)
        result.append({"pyeong": pyeong, "avg_exclusive_area": round(avg_area, 2), "trades": g})
    return result


def _weighted_regression_estimate(points: list[tuple[float, float]], today_x: float):
    """points: (x_days, price). 실패하면 None 반환."""
    n = len(points)
    sw = sx = sy = sxx = sxy = 0.0
    for x, y in points:
        age = today_x - x
        w = math.exp(-DECAY_LAMBDA * age)
        sw += w
        sx += w * x
        sy += w * y
        sxx += w * x * x
        sxy += w * x * y

    denom = sw * sxx - sx * sx
    if abs(denom) < 1e-9 or n < 3:
        return None
    b = (sw * sxy - sx * sy) / denom
    a = (sy - b * sx) / sw
    return a + b * today_x


def _weighted_average(points: list[tuple[float, float]], today_x: float) -> float:
    sw = swy = 0.0
    for x, y in points:
        age = today_x - x
        w = math.exp(-DECAY_LAMBDA * age)
        sw += w
        swy += w * y
    return swy / sw if sw else float("nan")


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    idx = (len(sorted_vals) - 1) * p
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return sorted_vals[int(idx)]
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def estimate_group(group: dict, today: date | None = None) -> dict:
    today = today or date.today()
    trades = group["trades"]

    dated = []
    for t in trades:
        d = _parse_deal_date(t)
        if d and t.get("deal_amount_10k"):
            dated.append((d, t["deal_amount_10k"]))
    dated.sort(key=lambda x: x[0])

    if not dated:
        return {
            **{k: v for k, v in group.items() if k != "trades"},
            "sample_count": 0,
            "confidence": "low",
            "fair_price_10k": None,
            "last_deal_price_10k": None,
            "last_deal_date": None,
            "peak_price_10k": None,
            "peak_date": None,
            "is_breakout": None,
            "gap_to_peak_pct": None,
            "price_band_10k": None,
        }

    origin = dated[0][0]
    points = [((d - origin).days, price) for d, price in dated]
    today_x = (today - origin).days

    trend = _weighted_regression_estimate(points, today_x)
    fallback_avg = _weighted_average(points, today_x)
    fair_price = trend if trend is not None else fallback_avg

    one_year_ago = today - timedelta(days=365)
    three_year_ago = today - timedelta(days=365 * 3)
    recent_1y = [p for d, p in dated if d >= one_year_ago]
    recent_3y = [p for d, p in dated if d >= three_year_ago]

    band_source = sorted(recent_1y) if len(recent_1y) >= 3 else sorted(recent_3y) or sorted(
        p for _, p in dated
    )

    last_date, last_price = dated[-1]

    # 전고점(조회 가능한 최근 3년 내 최고가) 돌파 여부 — 그 이전 진짜 역대 최고가는
    # 공공데이터로 알 수 없어 "최근 3년 기준"임을 명시한다.
    peak_date, peak_price = max(dated, key=lambda x: x[1])
    is_breakout = last_price >= peak_price
    gap_to_peak_pct = round((last_price - peak_price) / peak_price * 100, 1)

    n_1y = len(recent_1y)
    if n_1y >= 8:
        confidence = "high"
    elif n_1y >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "pyeong": group["pyeong"],
        "avg_exclusive_area": group["avg_exclusive_area"],
        "sample_count": len(dated),
        "sample_count_1y": n_1y,
        "sample_count_3y": len(recent_3y),
        "confidence": confidence,
        "fair_price_10k": round(fair_price) if fair_price == fair_price else None,  # NaN check
        "used_trend_regression": trend is not None,
        "last_deal_price_10k": last_price,
        "last_deal_date": last_date.isoformat(),
        "peak_price_10k": peak_price,
        "peak_date": peak_date.isoformat(),
        "is_breakout": is_breakout,
        "gap_to_peak_pct": gap_to_peak_pct,
        "price_band_10k": {
            "p25": round(_percentile(band_source, 0.25)),
            "p50": round(_percentile(band_source, 0.5)),
            "p75": round(_percentile(band_source, 0.75)),
        } if band_source else None,
        "trades_1y": [{"date": d.isoformat(), "price_10k": p} for d, p in dated if d >= one_year_ago],
        "trades_3y": [{"date": d.isoformat(), "price_10k": p} for d, p in dated if d >= three_year_ago],
    }


def build_valuation(trades: list[dict]) -> list[dict]:
    groups = cluster_by_pyeong(trades)
    return [estimate_group(g) for g in groups]


def estimate_jeonse_group(group: dict, today: date | None = None) -> dict | None:
    """평형 그룹의 최근 1년 순수 전세(월세 0원) 보증금 중앙값을 계산한다."""
    today = today or date.today()
    one_year_ago = today - timedelta(days=365)

    dated = []
    for t in group["trades"]:
        if not t.get("is_jeonse"):
            continue
        d = _parse_deal_date(t)
        if d and d >= one_year_ago and t.get("deposit_10k"):
            dated.append((d, t["deposit_10k"]))

    if not dated:
        return None

    dated.sort(key=lambda x: x[0])
    prices = sorted(p for _, p in dated)
    latest_date, latest_price = dated[-1]
    return {
        "pyeong": group["pyeong"],
        "avg_exclusive_area": group["avg_exclusive_area"],
        "jeonse_median_10k": round(_percentile(prices, 0.5)),
        "jeonse_max_10k": max(prices),
        "jeonse_latest_10k": latest_price,
        "jeonse_latest_date": latest_date.isoformat(),
        "jeonse_sample_count_1y": len(dated),
        "jeonse_trades_1y": [{"date": d.isoformat(), "price_10k": p} for d, p in dated],
    }


def build_jeonse_summary(rent_trades: list[dict]) -> list[dict]:
    groups = cluster_by_pyeong(rent_trades)
    results = [estimate_jeonse_group(g) for g in groups]
    return [r for r in results if r is not None]
