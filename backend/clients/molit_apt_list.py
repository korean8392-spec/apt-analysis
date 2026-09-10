"""국토교통부 공동주택 단지 목록제공 서비스 (AptListService4) 클라이언트.

시군구코드로 관내 공동주택 단지 목록(단지코드/단지명/법정동주소)을 가져온다.
이 API는 식별정보만 제공한다 — 세대수/동수/사용승인일 등은
AptBasisInfoServiceV5(molit_apt_basis.py)에서 kaptCode로 별도 조회해야 한다.

실제 호출로 확인된 사양(문서상 표기와 다른 부분이 있어 라이브 테스트로 확정함):
- Base URL: https://apis.data.go.kr/1613000/AptListService4/getSigunguAptList4
- 쿼리 파라미터는 `sigunguCd`가 아니라 `sigunguCode`
- 응답은 response.body.items 가 바로 배열(JSON) — item 래핑 없음
"""

import httpx

from config import MOLIT_SERVICE_KEY
import db

BASE_URL = "https://apis.data.go.kr/1613000/AptListService4/getSigunguAptList4"


async def get_apt_list(sigungu_cd: str) -> list[dict]:
    cache_key = f"molit_apt_list:{sigungu_cd}"
    cached = db.cache_get(cache_key, max_age_sec=60 * 60 * 24 * 30)
    if cached is not None:
        return cached

    params = {
        "serviceKey": MOLIT_SERVICE_KEY,
        "sigunguCode": sigungu_cd,
        "numOfRows": "3000",
        "pageNo": "1",
        "type": "json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(BASE_URL, params=params)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as e:
            raise RuntimeError(
                f"AptListService4 응답이 JSON이 아닙니다. 본문 앞부분: {resp.text[:300]}"
            ) from e

    if "OpenAPI_ServiceResponse" in data:
        err = data["OpenAPI_ServiceResponse"]["cmmMsgHeader"]
        raise RuntimeError(f"AptListService4 API 오류: {err.get('returnAuthMsg', err)}")

    try:
        body = data["response"]["body"]
        rows = body.get("items") or []
    except (KeyError, TypeError):
        header = data.get("response", {}).get("header", {})
        raise RuntimeError(f"AptListService4 API 오류: {header.get('resultMsg', data)}")

    normalized = []
    for row in rows:
        normalized.append(
            {
                "kapt_code": row.get("kaptCode"),
                "name": (row.get("kaptName") or "").strip(),
                "address": " ".join(
                    filter(None, [row.get("as1"), row.get("as2"), row.get("as3"), row.get("as4")])
                ).strip(),
                "bjd_code": row.get("bjdCode"),
            }
        )
    db.cache_set(cache_key, normalized)
    return normalized


def normalize_name(name: str) -> str:
    """단지명 비교용 정규화: 공백/괄호/특수문자 제거."""
    import re

    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"[^0-9A-Za-z가-힣]", "", name)
    return name.strip().lower()


def fuzzy_name_matches(query_norm: str, candidate_norm: str) -> bool:
    """정규화된 두 단지명이 같은 단지를 가리키는지 판단한다.

    실거래가 데이터의 아파트명은 등록기관마다 표기가 달라(예: 검색어 "삼환로즈빌" vs
    공식명 "고척삼환로즈빌") 부분 포함 매칭이 필요하지만, 무제한 허용하면 "삼환" 같은
    짧고 일반적인 이름의 완전히 다른 단지까지 오매칭된다(실제로 발견된 버그).
    그래서 후보명이 검색어보다 짧은 경우(역방향 포함)는 최소 길이와 길이 비율 기준을
    둬서 오탐을 막는다.
    """
    if not query_norm or not candidate_norm:
        return False
    if query_norm == candidate_norm:
        return True
    if query_norm in candidate_norm:
        return True
    if candidate_norm in query_norm:
        return len(candidate_norm) >= 3 and len(candidate_norm) >= len(query_norm) * 0.5
    return False


def find_candidates(apt_list: list[dict], query_name: str, limit: int = 5) -> list[dict]:
    """검색어와 이름이 비슷한 단지 후보를 관련도순으로 반환한다(사용자 확인용)."""
    target = normalize_name(query_name)
    if not target:
        return []
    exact = [a for a in apt_list if normalize_name(a["name"]) == target]
    partial = [
        a for a in apt_list
        if a not in exact and fuzzy_name_matches(target, normalize_name(a["name"]))
    ]
    partial.sort(key=lambda a: abs(len(normalize_name(a["name"])) - len(target)))
    return (exact + partial)[:limit]


def find_best_match(apt_list: list[dict], query_name: str) -> dict | None:
    candidates = find_candidates(apt_list, query_name, limit=1)
    return candidates[0] if candidates else None


def find_by_kapt_code(apt_list: list[dict], kapt_code: str) -> dict | None:
    for a in apt_list:
        if a.get("kapt_code") == kapt_code:
            return a
    return None
