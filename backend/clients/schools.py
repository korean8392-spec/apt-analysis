"""인근 초·중학교 조회 (카카오 로컬 API, category_group_code=SC4).

주의: 이건 "가장 가까운" 학교일 뿐 실제 "배정" 학교가 아니다. 배정은 각
교육지원청이 정한 통학구역 경계로 결정되는데, 이 경계 데이터는 공개 API로
제공되지 않는다(호갱노노 등도 비공개 계약 데이터를 쓰는 것으로 보임). 그래서
결과에 항상 "배정 학교 아님"이 드러나도록 프런트에서 표시해야 한다.
"""

import httpx

import db
from config import KAKAO_REST_KEY

CATEGORY_URL = "https://dapi.kakao.com/v2/local/search/category.json"
SEARCH_RADIUS_M = 1500
CACHE_SEC = 60 * 60 * 24 * 180


def _round_coord(v: float) -> float:
    return round(v, 4)


async def find_nearby_schools(lat: float, lon: float) -> dict | None:
    if not KAKAO_REST_KEY:
        return None

    cache_key = f"schools:{_round_coord(lat)}:{_round_coord(lon)}"
    cached = db.cache_get(cache_key, max_age_sec=CACHE_SEC)
    if cached is not None:
        return cached or None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                CATEGORY_URL,
                headers={"Authorization": f"KakaoAK {KAKAO_REST_KEY}"},
                params={
                    "category_group_code": "SC4",
                    "x": lon,
                    "y": lat,
                    "radius": SEARCH_RADIUS_M,
                    "sort": "distance",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None

    docs = data.get("documents") or []
    elementary = next((d for d in docs if "초등학교" in d.get("category_name", "")), None)
    middle = next((d for d in docs if "중학교" in d.get("category_name", "")), None)

    if not elementary and not middle:
        db.cache_set(cache_key, None)
        return None

    def _fmt(doc):
        if not doc:
            return None
        return {"name": doc["place_name"], "distance_m": round(float(doc["distance"]))}

    result = {"elementary": _fmt(elementary), "middle": _fmt(middle)}
    db.cache_set(cache_key, result)
    return result
