"""국토교통부 공동주택 기본 정보제공 서비스 (AptBasisInfoServiceV5) 클라이언트.

kaptCode로 단지의 세대수/동수/사용승인일/연면적 등 기본정보를 조회한다.
data.go.kr에서 이 API("국토교통부_공동주택 기본 정보제공 서비스", 15058453)를 별도로
활용신청해야 동작한다 — 신청 전에는 SERVICE_KEY_IS_NOT_REGISTERED_ERROR가 발생하며,
이 경우 상위 레이어에서 이 정보 없이 진행하도록 ApiNotRegisteredError로 감싼다.

실제 호출로 확인된 사양: 목록 API(AptListService4)와 달리 이 API는 kaptCode 단건 조회라
응답이 response.body.items(배열)가 아니라 response.body.item(단일 객체)로 온다.
건폐율/용적률 필드는 이 API에 없다 — 건축물대장 소관이라 여기서는 제공하지 않는다.
"""

import httpx

from config import MOLIT_SERVICE_KEY
import db

BASE_URL = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV5/getAphusBassInfoV5"


class ApiNotRegisteredError(RuntimeError):
    pass


async def get_apt_basis(kapt_code: str) -> dict | None:
    cache_key = f"molit_apt_basis:{kapt_code}"
    cached = db.cache_get(cache_key, max_age_sec=60 * 60 * 24 * 90)
    if cached is not None:
        return cached

    params = {
        "serviceKey": MOLIT_SERVICE_KEY,
        "kaptCode": kapt_code,
        "numOfRows": "1",
        "pageNo": "1",
        "type": "json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(BASE_URL, params=params)
        # 미등록 서비스키 등의 오류는 200이 아닌 상태코드(403 등)로 오면서도 본문에
        # 원인이 담긴 JSON을 주는 경우가 있어, raise_for_status보다 먼저 JSON 파싱을 시도한다.
        try:
            data = resp.json()
        except ValueError as e:
            resp.raise_for_status()
            raise RuntimeError(
                f"AptBasisInfoServiceV5 응답이 JSON이 아닙니다. 본문: {resp.text[:300]}"
            ) from e

    if "OpenAPI_ServiceResponse" in data:
        err = data["OpenAPI_ServiceResponse"]["cmmMsgHeader"]
        if err.get("errMsg") == "SERVICE_KEY_IS_NOT_REGISTERED_ERROR":
            raise ApiNotRegisteredError(
                "data.go.kr에서 '국토교통부_공동주택 기본 정보제공 서비스'를 활용신청해야 "
                "세대수/동수/사용승인일이 자동으로 채워집니다."
            )
        raise RuntimeError(f"AptBasisInfoServiceV5 API 오류: {err.get('returnAuthMsg', err)}")

    body = data.get("response", {}).get("body", {})
    row = body.get("item")
    if isinstance(row, list):
        row = row[0] if row else None
    if not row:
        rows = body.get("items") or []
        if isinstance(rows, dict):
            rows = [rows]
        row = rows[0] if rows else None
    if not row:
        return None

    use_date = row.get("kaptUsedate")  # "20000731" 형식
    use_date_fmt = (
        f"{use_date[:4]}-{use_date[4:6]}-{use_date[6:8]}"
        if use_date and len(use_date) == 8
        else use_date
    )

    household_cnt = row.get("kaptdaCnt")
    if isinstance(household_cnt, float):
        household_cnt = int(household_cnt)

    result = {
        "kapt_code": row.get("kaptCode"),
        "name": row.get("kaptName"),
        "address": row.get("kaptAddr"),
        "road_address": row.get("doroJuso"),
        "dong_cnt": row.get("kaptDongCnt"),
        "household_cnt": household_cnt,
        "use_approval_date": use_date_fmt,
        "total_floor_area": row.get("kaptTarea"),
        "top_floor": row.get("kaptTopFloor"),
        "heat_type": row.get("codeHeatNm"),
        "_raw": row,
    }
    db.cache_set(cache_key, result)
    return result
