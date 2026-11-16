import logging
import os
import re
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import httpx
from config import MOLIT_SERVICE_KEY, has_molit_key
from services import complex_search, liquidity, screening, summary

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("appt")

app = FastAPI(title="네이버 아파트 분석")


_SECRET_RE = re.compile(r"(serviceKey|appkey|KakaoAK)[=\s]*[^&\s'\"]+", re.IGNORECASE)


def _redact(text: str) -> str:
    # 공공데이터/카카오 키가 로그나 화면에 새지 않게 가린다.
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", text)


def _network_error(e: Exception, context: str) -> HTTPException:
    # 정부/카카오 API 쪽 일시적 네트워크 오류(ConnectTimeout 등)가 여기로 흘러든다 —
    # 재시도하면 대부분 바로 해결되는 것을 실제로 확인했다. 사용자에게는 자세한 원인 대신
    # 오류 종류(클래스 이름)만 알려주고(키가 들어 있을 수 있는 메시지/주소는 숨김), 서버 로그에는
    # 키를 가린 메시지와 스택트레이스를 남긴다.
    try:
        # httpx 예외의 request는 값이 없으면 AttributeError가 아니라 RuntimeError를 던진다.
        url = e.request.url if hasattr(e, "request") else None
    except RuntimeError:
        url = None
    logger.error(
        "%s failed unexpectedly: %s: %s url=%s",
        context, type(e).__name__, _redact(str(e)), _redact(str(url)) if url else None,
    )
    return HTTPException(
        status_code=503,
        detail=f"일시적인 네트워크 오류로 조회에 실패했습니다. 잠시 후 다시 시도해주세요. (오류 종류: {type(e).__name__})",
    )

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.on_event("startup")
def _startup():
    db.init_db()


# 외부 감시 서비스(UptimeRobot 등)가 주기적으로 호출해 무료 서버가 잠들지 않게 하는 용도라,
# HEAD로 확인하는 서비스도 정상으로 인식되도록 GET과 HEAD를 모두 받는다.
@app.api_route("/api/health", methods=["GET", "HEAD"])
def health():
    # RENDER_GIT_COMMIT은 Render가 배포 시 자동으로 넣어주는 값이라, 지금 서버가 어떤 커밋인지 확인할 수 있다.
    return {
        "ok": True,
        "molit_key_configured": has_molit_key(),
        "commit": os.getenv("RENDER_GIT_COMMIT", "")[:7] or None,
    }


_diag_cache: dict = {"at": 0.0, "data": None}


@app.get("/api/diag/molit")
async def diag_molit():
    """국토부 실거래 API에 지금 연결이 되는지 한 번 확인한다(연결 문제 진단용).

    키나 주소는 응답에 넣지 않고, 외부 호출을 남용당하지 않도록 60초 동안은 이전 결과를 재사용한다."""
    now = time.time()
    if _diag_cache["data"] and now - _diag_cache["at"] < 60:
        return {**_diag_cache["data"], "cached": True}
    from datetime import date

    ym = f"{date.today().year}{date.today().month:02d}"
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
            resp = await client.get(
                "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade",
                params={"serviceKey": MOLIT_SERVICE_KEY, "LAWD_CD": "11680", "DEAL_YMD": ym, "numOfRows": "1", "pageNo": "1"},
            )
        code = re.search(r"<resultCode>(.*?)</resultCode>", resp.text)
        msg = re.search(r"<(?:resultMsg|returnAuthMsg)>(.*?)</(?:resultMsg|returnAuthMsg)>", resp.text)
        data = {
            "ok": resp.status_code == 200 and code is not None and code.group(1) == "000",
            "http_status": resp.status_code,
            "result_code": code.group(1) if code else None,
            "result_msg": _redact(msg.group(1)) if msg else None,
            "seconds": round(time.time() - started, 2),
        }
    except Exception as e:  # noqa: BLE001 - 진단용이라 어떤 오류든 종류만 알려준다
        data = {"ok": False, "error": type(e).__name__, "seconds": round(time.time() - started, 2)}
    _diag_cache.update(at=time.time(), data=data)
    return {**data, "cached": False}


@app.get("/api/find-complex")
async def find_complex(sigungu: str, apt_name: str):
    try:
        return await complex_search.find_complex_candidates(sigungu, apt_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/complex-parcels")
async def complex_parcels(sigungu: str, kapt_code: str):
    try:
        return await complex_search.list_registry_parcels(sigungu, kapt_code)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _network_error(e, "complex_parcels")


@app.get("/api/search")
async def search(
    sigungu: str,
    apt_name: str,
    kapt_code: str | None = None,
    jibun: str | None = Query(None, pattern=r"^\d{1,4}(-\d{1,4})?$"),
):
    try:
        result = await complex_search.search_complex(sigungu, apt_name, kapt_code, jibun)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _network_error(e, "search")
    manual_info = db.get_manual_complex_info(result["complex_key"])
    manual_listings = db.get_manual_listings(result["complex_key"])
    result["manual_complex_info"] = manual_info
    result["manual_listings"] = manual_listings
    result["summary_text"] = summary.build_summary_text(
        result["matched_apt_name"],
        result["sigungu"],
        result["basis_info"],
        result["valuations"],
        manual_listings,
    )
    return result


@app.get("/api/liquidity-ranking")
async def liquidity_ranking(
    sigungu: str, price_min: float | None = None, price_max: float | None = None
):
    if not has_molit_key():
        raise HTTPException(
            status_code=400,
            detail="공공데이터포털 서비스키(MOLIT_SERVICE_KEY)가 설정되지 않았습니다.",
        )
    try:
        return await liquidity.rank_liquidity(sigungu, price_min, price_max)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _network_error(e, "liquidity_ranking")


@app.get("/api/budget-screening")
async def budget_screening(sigungu: str, max_price: float):
    if not has_molit_key():
        raise HTTPException(
            status_code=400,
            detail="공공데이터포털 서비스키(MOLIT_SERVICE_KEY)가 설정되지 않았습니다.",
        )
    try:
        return await screening.screen_by_budget(sigungu, max_price)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _network_error(e, "budget_screening")


class ManualListingIn(BaseModel):
    complex_key: str
    pyeong_label: str
    exclusive_area: float | None = None
    listing_count: int | None = None
    ask_price_min: float | None = None
    ask_price_max: float | None = None
    memo: str | None = None


@app.post("/api/manual-listings")
def upsert_manual_listing(payload: ManualListingIn):
    db.upsert_manual_listing(
        payload.complex_key,
        payload.pyeong_label,
        payload.exclusive_area,
        payload.listing_count,
        payload.ask_price_min,
        payload.ask_price_max,
        payload.memo,
    )
    return {"ok": True}


@app.get("/api/manual-listings")
def list_manual_listings(complex_key: str):
    return db.get_manual_listings(complex_key)


@app.delete("/api/manual-listings/{listing_id}")
def remove_manual_listing(listing_id: int):
    db.delete_manual_listing(listing_id)
    return {"ok": True}


class ManualComplexInfoIn(BaseModel):
    complex_key: str
    building_coverage_ratio: float | None = None
    floor_area_ratio: float | None = None
    household_cnt: int | None = None
    use_approval_year: int | None = None
    memo: str | None = None


@app.post("/api/manual-complex-info")
def upsert_manual_complex_info(payload: ManualComplexInfoIn):
    db.upsert_manual_complex_info(
        payload.complex_key,
        payload.building_coverage_ratio,
        payload.floor_area_ratio,
        payload.household_cnt,
        payload.use_approval_year,
        payload.memo,
    )
    return {"ok": True}


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
