"""국토교통부 건축HUB 건축물대장정보 서비스 (BldRgstHubService).

건폐율(bcRat)/용적률(vlRat)은 개별 동의 "표제부"(getBrTitleInfo)가 아니라 단지 전체
기준인 "총괄표제부"(getBrRecapTitleInfo)에 들어있다 — 실제 호출로 확인한 결과, 아파트
단지처럼 여러 동으로 구성된 집합건물은 개별 동 표제부의 bcRat/vlRat이 0으로 비어있고,
총괄표제부에만 대지면적 대비 실제 값이 채워져 있었다. 그래도 개별 동 표제부에 값이
채워져 있는 단지도 있을 수 있어(오래된/단독 등록 사례) 참고용으로 함께 제공한다.

지번(시군구코드+법정동코드+대지구분+본번+부번) 기준으로 조회하며, 본번/부번은 4자리로
0-패딩해야 매칭된다(실제 호출로 확인).
data.go.kr에서 "국토교통부_건축HUB_건축물대장정보 서비스"를 별도로 활용신청해야 동작한다.
"""

import httpx

from config import MOLIT_SERVICE_KEY
import db

RECAP_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrRecapTitleInfo"
TITLE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"


class ApiNotRegisteredError(RuntimeError):
    pass


def _clean_float(raw) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        return None


def _extract_rows(body: dict) -> list[dict]:
    items = body.get("items")
    if isinstance(items, dict):
        item = items.get("item")
        if isinstance(item, list):
            return item
        if isinstance(item, dict):
            return [item]
    if isinstance(items, list):
        return items
    single = body.get("item")
    if isinstance(single, dict):
        return [single]
    if isinstance(single, list):
        return single
    return []


async def _call(url: str, params: dict) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        try:
            data = resp.json()
        except ValueError as e:
            resp.raise_for_status()
            raise RuntimeError(
                f"BldRgstHubService 응답이 JSON이 아닙니다. 본문: {resp.text[:300]}"
            ) from e

    if "OpenAPI_ServiceResponse" in data:
        err = data["OpenAPI_ServiceResponse"]["cmmMsgHeader"]
        if err.get("errMsg") == "SERVICE_KEY_IS_NOT_REGISTERED_ERROR":
            raise ApiNotRegisteredError(
                "data.go.kr에서 '국토교통부_건축HUB_건축물대장정보 서비스'를 활용신청해야 "
                "건폐율/용적률이 자동으로 채워집니다."
            )
        raise RuntimeError(f"BldRgstHubService API 오류: {err.get('returnAuthMsg', err)}")

    return _extract_rows(data.get("response", {}).get("body", {}))


async def get_building_recap_info(
    sigungu_cd: str, bjdong_cd: str, bun: str, ji: str
) -> dict | None:
    cache_key = f"molit_building_recap:{sigungu_cd}:{bjdong_cd}:{bun}:{ji}"
    cached = db.cache_get(cache_key, max_age_sec=60 * 60 * 24 * 180)
    if cached is not None:
        return cached

    params = {
        "serviceKey": MOLIT_SERVICE_KEY,
        "sigunguCd": sigungu_cd,
        "bjdongCd": bjdong_cd,
        "platGbCd": "0",
        "bun": bun,
        "ji": ji,
        "numOfRows": "5",
        "pageNo": "1",
        "_type": "json",
    }
    rows = await _call(RECAP_URL, params)
    if not rows:
        return None

    row = rows[0]
    plat_area = _clean_float(row.get("platArea"))
    # 오래된(주공 등) 단지는 총괄표제부에 대지면적 자체가 등록 안 된 경우가 있는데, 이때
    # bcRat/vlRat/totPkngCnt가 진짜 0이 아니라 "미등록"을 의미하는 0으로 나온다.
    # 대지면적이 0이면 신뢰할 수 없는 값으로 보고 None 처리해 "0%"로 오인되지 않게 한다.
    has_reliable_ratios = bool(plat_area)
    result = {
        "building_coverage_ratio": _clean_float(row.get("bcRat")) if has_reliable_ratios else None,
        "floor_area_ratio": _clean_float(row.get("vlRat")) if has_reliable_ratios else None,
        "plat_area": plat_area,
        "arch_area": _clean_float(row.get("archArea")),
        "total_area": _clean_float(row.get("totArea")),
        "household_cnt": row.get("hhldCnt") or None,
        "main_bld_cnt": row.get("mainBldCnt"),
        "total_parking_cnt": row.get("totPkngCnt") or None,
        "_raw": row,
    }
    db.cache_set(cache_key, result)
    return result


async def get_building_recap_info_best(
    sigungu_cd: str, bjdong_cd: str, jibun_candidates: list[str]
) -> dict | None:
    """복수의 지번 후보 중 가장 대표성 있는(건폐율/용적률이 채워진, 없으면 세대수가 가장
    큰) 총괄표제부 레코드를 골라 반환한다 — 여러 필지로 구성된 대형/구축 단지는 지번마다
    등록 상태가 달라, 우연히 고른 지번이 일부 동만 담은 부실한 레코드일 수 있다(실제
    사례: 상계주공3단지 730-2는 98세대만 담긴 레코드, 737번지가 2115세대짜리 대표 레코드)."""
    results = []
    for jibun_token in jibun_candidates:
        if "-" in jibun_token:
            bun, ji = jibun_token.split("-", 1)
        else:
            bun, ji = jibun_token, "0"
        info = await get_building_recap_info(sigungu_cd, bjdong_cd, bun.zfill(4), ji.zfill(4))
        if info:
            results.append(info)

    if not results:
        return None

    with_ratios = [r for r in results if r["building_coverage_ratio"] and r["floor_area_ratio"]]
    if with_ratios:
        return max(with_ratios, key=lambda r: r["household_cnt"] or 0)
    return max(results, key=lambda r: r["household_cnt"] or 0)


async def get_building_title_list(
    sigungu_cd: str, bjdong_cd: str, bun: str, ji: str
) -> list[dict]:
    """개별 동 표제부 목록(참고용) — 주건축물(mainAtchGbCd=='0')만 반환한다.
    이 단지에서는 값이 비어있어도(0) 다른 단지에서는 채워져 있을 수 있어 있는 그대로 보여준다."""
    cache_key = f"molit_building_title_list:{sigungu_cd}:{bjdong_cd}:{bun}:{ji}"
    cached = db.cache_get(cache_key, max_age_sec=60 * 60 * 24 * 180)
    if cached is not None:
        return cached

    params = {
        "serviceKey": MOLIT_SERVICE_KEY,
        "sigunguCd": sigungu_cd,
        "bjdongCd": bjdong_cd,
        "platGbCd": "0",
        "bun": bun,
        "ji": ji,
        "numOfRows": "100",
        "pageNo": "1",
        "_type": "json",
    }
    rows = await _call(TITLE_URL, params)

    result = []
    for row in rows:
        if row.get("mainAtchGbCd") != "0":
            continue
        result.append(
            {
                "dong_name": row.get("dongNm"),
                "building_coverage_ratio": _clean_float(row.get("bcRat")),
                "floor_area_ratio": _clean_float(row.get("vlRat")),
                "household_cnt": row.get("hhldCnt"),
                "ground_floor_cnt": row.get("grndFlrCnt"),
                "underground_floor_cnt": row.get("ugrndFlrCnt"),
            }
        )
    db.cache_set(cache_key, result)
    return result
