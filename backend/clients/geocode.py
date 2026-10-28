"""주소 -> 좌표 변환.

1순위는 카카오 로컬 API(주소 검색 -> 키워드 검색)다. OSM Nominatim은 한국 주소를
단지 위치가 아닌 인근 도로/동 중심으로 잡는 경우가 있어(예: 래미안원베일리가 한강 위
도로로 표시됨) 카카오가 실패하거나 키가 없을 때의 폴백으로만 쓴다.

Nominatim 이용정책(초당 1회 이하, 식별 가능한 User-Agent 필수)을 지키기 위해
서버에서 한 번만 호출하고 결과를 장기 캐시한다.
"""

import asyncio
import httpx

import db
from config import KAKAO_REST_KEY

BASE_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "naver-apt-analyzer/0.1 (personal local use)"
KAKAO_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
CACHE_SEC = 60 * 60 * 24 * 365

_last_call_at = 0.0
_lock = asyncio.Lock()


async def _kakao_search(url: str, query: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"KakaoAK {KAKAO_REST_KEY}"},
                params={"query": query, "size": 1},
            )
            resp.raise_for_status()
            docs = resp.json().get("documents") or []
    except Exception:
        return None
    if not docs:
        return None
    doc = docs[0]
    return {
        "lat": float(doc["y"]),
        "lon": float(doc["x"]),
        "display_name": doc.get("address_name") or doc.get("place_name"),
    }


async def _geocode_kakao(address: str) -> dict | None:
    if not KAKAO_REST_KEY:
        return None
    # 주소 검색이 안 되면(단지명이 섞인 문자열 등) 키워드 검색으로 단지 자체를 찾는다.
    return await _kakao_search(KAKAO_ADDRESS_URL, address) or await _kakao_search(
        KAKAO_KEYWORD_URL, address
    )


async def _geocode_nominatim(address: str) -> dict | None:
    cache_key = f"geocode:{address}"
    cached = db.cache_get(cache_key, max_age_sec=CACHE_SEC)
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


async def geocode(address: str) -> dict | None:
    address = (address or "").strip()
    if not address:
        return None

    # 카카오 결과만 v2 키에 캐시한다. 예전 Nominatim 결과가 남은 "geocode:{address}"
    # 캐시는 읽지 않아야 잘못 저장된 좌표가 계속 쓰이지 않는다.
    kakao_key = f"geocode:v2:{address}"
    cached = db.cache_get(kakao_key, max_age_sec=CACHE_SEC)
    if cached:
        return cached

    result = await _geocode_kakao(address)
    if result:
        db.cache_set(kakao_key, result)
        return result

    return await _geocode_nominatim(address)
