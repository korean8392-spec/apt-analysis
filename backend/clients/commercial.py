"""인근 상권 밀집도 (카카오 로컬 API 카테고리별 개수).

"강남역 상권"처럼 이름 붙은 상권 경계 데이터는 공개 API로 제공되지 않는다.
그 대신 반경 500m 내 음식점/카페/편의점 개수를 상권 밀집도의 근사치로 쓴다.
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


async def _count(client: httpx.AsyncClient, category_code: str, lat: float, lon: float) -> int:
    resp = await client.get(
        CATEGORY_URL,
        headers={"Authorization": f"KakaoAK {KAKAO_REST_KEY}"},
        params={
            "category_group_code": category_code,
            "x": lon,
            "y": lat,
            "radius": RADIUS_M,
            "size": 1,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("meta", {}).get("total_count", 0)


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
            counts = await asyncio.gather(
                *[_count(client, CATEGORIES[label], lat, lon) for label in labels]
            )
    except Exception:
        return None

    result = {"radius_m": RADIUS_M, **dict(zip(labels, counts))}
    db.cache_set(cache_key, result)
    return result
