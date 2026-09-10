"""인근 지하철역 조회 (OpenStreetMap Overpass API, 무료/키 불필요).

단지 좌표(지오코딩 결과) 기준 반경 내 가장 가까운 역과 그 역을 지나는 노선을
Overpass에서 조회한다. 역/노선 구성은 자주 바뀌지 않으므로 장기 캐시한다.
"""

import math
import httpx

import db

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
SEARCH_RADIUS_M = 2000
# User-Agent 없이 호출하면 406 Not Acceptable을 반환한다(실제로 확인됨).
_HEADERS = {"User-Agent": "naver-apt-analyzer/0.1 (personal local use)", "Accept": "*/*"}


def _round_coord(v: float) -> float:
    # 좌표를 4자리(약 11m 단위)로 반올림해 캐시 키를 안정시킨다.
    return round(v, 4)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def _overpass(query: str) -> dict:
    async with httpx.AsyncClient(timeout=20, headers=_HEADERS) as client:
        resp = await client.post(OVERPASS_URL, data={"data": query})
        resp.raise_for_status()
        return resp.json()


async def find_nearest_station(lat: float, lon: float) -> dict | None:
    cache_key = f"subway:{_round_coord(lat)}:{_round_coord(lon)}"
    cached = db.cache_get(cache_key, max_age_sec=60 * 60 * 24 * 180)
    if cached is not None:
        return cached or None

    try:
        station_query = f"""
        [out:json][timeout:20];
        node["railway"="station"](around:{SEARCH_RADIUS_M},{lat},{lon});
        out body;
        """
        data = await _overpass(station_query)
        elements = data.get("elements") or []
        candidates = [e for e in elements if e.get("tags", {}).get("name")]
        if not candidates:
            db.cache_set(cache_key, None)
            return None

        best = min(
            candidates,
            key=lambda e: _haversine_m(lat, lon, e["lat"], e["lon"]),
        )
        distance_m = round(_haversine_m(lat, lon, best["lat"], best["lon"]))
        station_name = best["tags"]["name"]

        # 노선 조회: "railway=station" 노드는 대개 route relation의 멤버가 아니라
        # 별도의 stop_position/platform이 멤버로 들어있어 rel(bn)으로는 못 찾는다
        # (실제 확인됨, 대치역 기준 rel(bn) 결과 0건). 대신 역 좌표 주변(250m)의
        # route=subway relation을 공간 검색으로 찾는 방식을 쓴다.
        lines: list[str] = []
        try:
            route_query = f"""
            [out:json][timeout:20];
            rel["route"="subway"](around:250,{best['lat']},{best['lon']});
            out tags;
            """
            route_data = await _overpass(route_query)
            for rel in route_data.get("elements") or []:
                tags = rel.get("tags", {})
                label = tags.get("ref")
                if label and label.isdigit():
                    label = f"{label}호선"
                if label and label not in lines:
                    lines.append(label)
        except Exception:
            pass

        result = {
            "station_name": station_name,
            "lines": lines,
            "distance_m": distance_m,
            "walk_minutes": round(distance_m / 67),  # 도보 약 67m/분 기준
        }
        db.cache_set(cache_key, result)
        return result
    except Exception:
        return None
