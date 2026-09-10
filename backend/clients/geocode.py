"""주소 -> 좌표 변환 (OpenStreetMap Nominatim, 무료/키 불필요).

Nominatim 이용정책(초당 1회 이하, 식별 가능한 User-Agent 필수)을 지키기 위해
서버에서 한 번만 호출하고 결과를 장기 캐시한다.
"""

import asyncio
import httpx

import db

BASE_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "naver-apt-analyzer/0.1 (personal local use)"

_last_call_at = 0.0
_lock = asyncio.Lock()


async def geocode(address: str) -> dict | None:
    address = (address or "").strip()
    if not address:
        return None

    cache_key = f"geocode:{address}"
    cached = db.cache_get(cache_key, max_age_sec=60 * 60 * 24 * 365)
    if cached is not None:
        return cached or None

    global _last_call_at
    async with _lock:
        wait = 1.1 - (asyncio.get_event_loop().time() - _last_call_at)
        if wait > 0:
            await asyncio.sleep(wait)

        params = {
            "q": address,
            "format": "json",
            "countrycodes": "kr",
            "limit": "1",
        }
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": USER_AGENT}) as client:
            resp = await client.get(BASE_URL, params=params)
            _last_call_at = asyncio.get_event_loop().time()
            resp.raise_for_status()
            results = resp.json()

    if not results:
        db.cache_set(cache_key, None)
        return None

    result = {
        "lat": float(results[0]["lat"]),
        "lon": float(results[0]["lon"]),
        "display_name": results[0].get("display_name"),
    }
    db.cache_set(cache_key, result)
    return result
