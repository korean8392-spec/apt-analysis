from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from config import has_molit_key
from services import complex_search, liquidity, screening, summary

app = FastAPI(title="네이버 아파트 분석")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.on_event("startup")
def _startup():
    db.init_db()


@app.get("/api/health")
def health():
    return {"ok": True, "molit_key_configured": has_molit_key()}


@app.get("/api/find-complex")
async def find_complex(sigungu: str, apt_name: str):
    try:
        return await complex_search.find_complex_candidates(sigungu, apt_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/search")
async def search(sigungu: str, apt_name: str, kapt_code: str | None = None):
    try:
        result = await complex_search.search_complex(sigungu, apt_name, kapt_code)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
