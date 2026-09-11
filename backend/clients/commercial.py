"""인근 상권 밀집도 (카카오 로컬 API 카테고리별 개수 + 지도 표시용 위치 샘플).

"강남역 상권"처럼 이름 붙은 상권 경계 데이터는 공개 API로 제공되지 않는다.
그 대신 반경 500m 내 음식점/카페/편의점 개수를 상권 밀집도의 근사치로 쓰고,
지도에 점으로 찍을 수 있도록 카테고리별 위치도 최대 15곳씩 함께 담는다
(카카오 API 한 페이지 최대치라 전수는 아니며, 개수와 점 표시용 샘플이다).
"""

import asyncio
import httpx

import db
from config import KAKAO_REST_KEY

CATEGORY_URL = "https://dapi.kakao.com/v2/local/search/category.json"
RADIUS_M = 500
CACHE_SEC = 60 * 60 * 24 * 90  # 상권 구성은 지하철/학교보다 자주 바뀌므로 좀 더 짧게 캐시
CATEGORIES = {"restaurant": "FD6", "cafe": "CE7", "convenience": "CS2"}


def _round_coord(v: float) -> float:
    return round(v, 4)


async def _fetch(client: httpx.AsyncClient, category_code: str, lat: float, lon: float) -> dict:
    resp = await client.get(
        CATEGORY_URL,
        headers={"Authorization": f"KakaoAK {KAKAO_REST_KEY}"},
        params={
            "category_group_code": category_code,
            "x": lon,
            "y": lat,
            "radius": RADIUS_M,
            "size": 15,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    count = data.get("meta", {}).get("total_count", 0)
    places = [
        {"name": doc["place_name"], "lat": float(doc["y"]), "lon": float(doc["x"])}
        for doc in data.get("documents") or []
    ]
    return {"count": count, "places": places}


async def get_commercial_density(lat: float, lon: float) -> dict | None:
    if not KAKAO_REST_KEY:
        return None

    cache_key = f"commercial:{_round_coord(lat)}:{_round_coord(lon)}"
    cached = db.cache_get(cache_key, max_age_sec=CACHE_SEC)
    if cached is not None:
        return cached or None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            labels = list(CATEGORIES.keys())
            fetched = await asyncio.gather(
                *[_fetch(client, CATEGORIES[label], lat, lon) for label in labels]
            )
    except Exception:
        return None

    result = {
        "radius_m": RADIUS_M,
        **{label: f["count"] for label, f in zip(labels, fetched)},
        "places": {label: f["places"] for label, f in zip(labels, fetched)},
    }
    db.cache_set(cache_key, result)
    return result
