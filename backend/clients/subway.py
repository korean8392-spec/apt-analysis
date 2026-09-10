"""인근 지하철역 조회 (카카오 로컬 API, category_group_code=SW8).

애초에 무료 OSM Overpass API로 구현했으나, 배포 환경(Render)에서
"ConnectError: All connection attempts failed"로 계속 실패하는 것을 실제로
확인했다 — 소규모 커뮤니티 서버라 클라우드/데이터센터 IP 대역을 막아둔 것으로
추정된다. 카카오 로컬 API는 상용 서비스라 이런 차단 위험이 낮고, 노선명까지
place_name에 바로 포함돼 있어 더 간단하고 정확하다.
"""

import httpx

import db
from config import KAKAO_REST_KEY

CATEGORY_URL = "https://dapi.kakao.com/v2/local/search/category.json"
SEARCH_RADIUS_M = 2000
CACHE_SEC = 60 * 60 * 24 * 180


def _round_coord(v: float) -> float:
    # 좌표를 4자리(약 11m 단위)로 반올림해 캐시 키를 안정시킨다.
    return round(v, 4)


async def find_nearest_station(lat: float, lon: float) -> dict | None:
    if not KAKAO_REST_KEY:
        return None

    cache_key = f"subway:{_round_coord(lat)}:{_round_coord(lon)}"
    cached = db.cache_get(cache_key, max_age_sec=CACHE_SEC)
    if cached is not None:
        return cached or None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                CATEGORY_URL,
                headers={"Authorization": f"KakaoAK {KAKAO_REST_KEY}"},
                params={
                    "category_group_code": "SW8",
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
    if not docs:
        db.cache_set(cache_key, None)
        return None

    nearest = docs[0]
    # place_name이 "대치역 3호선"처럼 "역명 + 노선"으로 오므로 노선만 뽑아내고,
    # 같은 역에서 갈아타는 다른 노선도 같은 거리 근방(±50m)에 있으면 함께 모은다.
    def line_of(doc):
        name = doc.get("place_name", "")
        station = doc.get("place_name", "").split(" ")[0]
        line = name[len(station):].strip()
        return station, (line or None)

    station_name, first_line = line_of(nearest)
    nearest_distance = float(nearest.get("distance") or 0)
    lines = [first_line] if first_line else []
    for doc in docs[1:]:
        name, line = line_of(doc)
        if name != station_name:
            continue
        if abs(float(doc.get("distance") or 0) - nearest_distance) > 80:
            continue
        if line and line not in lines:
            lines.append(line)

    result = {
        "station_name": station_name,
        "lines": lines,
        "distance_m": round(nearest_distance),
        "walk_minutes": round(nearest_distance / 67),  # 도보 약 67m/분 기준
    }
    db.cache_set(cache_key, result)
    return result
