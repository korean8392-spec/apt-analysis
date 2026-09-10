import re
from collections import Counter

from config import has_molit_key
from clients import geocode as geocode_client
from clients import molit_apt_basis, molit_apt_list, molit_building, molit_rent, molit_trade
from services import dong_codes, valuation


def make_complex_key(sigungu_code: str, apt_name: str) -> str:
    return f"{sigungu_code}:{molit_apt_list.normalize_name(apt_name)}"


def _extract_jibun_token(addr: str) -> str | None:
    """'...동 1014-3 대치삼성'처럼 지번 뒤에 단지명이 붙은 문자열에서 지번 토큰만 뽑는다."""
    for tok in addr.split():
        if re.fullmatch(r"\d+(-\d+)?", tok):
            return tok
    return None


def _parse_bun_ji(jibun_token: str) -> tuple[str, str]:
    """건축HUB API는 본번/부번을 4자리로 0-패딩해야 매칭된다(실제 호출로 확인)."""
    if "-" in jibun_token:
        bun, ji = jibun_token.split("-", 1)
    else:
        bun, ji = jibun_token, "0"
    return bun.zfill(4), ji.zfill(4)


def _resolve_sigungu(sigungu_keyword: str) -> dict:
    sigungu_candidates = dong_codes.search_sigungu(sigungu_keyword)
    if not sigungu_candidates:
        raise ValueError(f"'{sigungu_keyword}'에 해당하는 서울/경기 시군구를 찾지 못했습니다.")
    if len(sigungu_candidates) > 1:
        options = ", ".join(f"{r['sido']} {r['sigungu']}" for r in sigungu_candidates)
        raise ValueError(f"시군구가 여러 개 검색되었습니다. 더 구체적으로 입력하세요: {options}")
    return sigungu_candidates[0]


async def find_complex_candidates(sigungu_keyword: str, apt_name: str) -> dict:
    """사용자가 입력한 단지명과 이름이 비슷한 후보들을 반환한다(확인용, 실거래가 조회 전)."""
    if not has_molit_key():
        raise RuntimeError(
            "공공데이터포털 서비스키(MOLIT_SERVICE_KEY)가 설정되지 않았습니다. "
            ".env 파일에 키를 넣고 서버를 재시작하세요."
        )
    sigungu = _resolve_sigungu(sigungu_keyword)
    apt_list = await molit_apt_list.get_apt_list(sigungu["code"])
    candidates = molit_apt_list.find_candidates(apt_list, apt_name, limit=5)
    return {
        "sigungu": {"sido": sigungu["sido"], "sigungu": sigungu["sigungu"], "code": sigungu["code"]},
        "candidates": candidates,
    }


async def search_complex(sigungu_keyword: str, apt_name: str, kapt_code: str | None = None) -> dict:
    if not has_molit_key():
        raise RuntimeError(
            "공공데이터포털 서비스키(MOLIT_SERVICE_KEY)가 설정되지 않았습니다. "
            ".env 파일에 키를 넣고 서버를 재시작하세요."
        )

    sigungu = _resolve_sigungu(sigungu_keyword)
    sigungu_cd = sigungu["code"]

    apt_list = await molit_apt_list.get_apt_list(sigungu_cd)
    matched = (
        molit_apt_list.find_by_kapt_code(apt_list, kapt_code)
        if kapt_code
        else molit_apt_list.find_best_match(apt_list, apt_name)
    )

    basis_info = None
    basis_info_error = None
    if matched and matched.get("kapt_code"):
        try:
            basis_info = await molit_apt_basis.get_apt_basis(matched["kapt_code"])
        except molit_apt_basis.ApiNotRegisteredError as e:
            basis_info_error = str(e)

    trades_raw = await molit_trade.get_trades(sigungu_cd, months=36)

    # 실거래가의 아파트명 표기는 등록기관마다 달라(예: 검색어 "삼환로즈빌" vs 공식명
    # "고척삼환로즈빌") 부분 포함 매칭이 필요하지만, AptListService4에서 이미 정식
    # 단지명을 알아냈다면 그걸 기준으로 매칭하는 게 사용자가 입력한 축약어보다 훨씬
    # 정확하다(짧고 일반적인 검색어일수록 다른 단지와 오매칭될 위험이 큼).
    target_norm = molit_apt_list.normalize_name(matched["name"] if matched else apt_name)
    matched_trades = [
        t for t in trades_raw
        if molit_apt_list.fuzzy_name_matches(target_norm, molit_apt_list.normalize_name(t["apt_name"]))
    ]

    if not matched_trades and not matched:
        raise ValueError(
            f"'{sigungu['sido']} {sigungu['sigungu']}'에서 '{apt_name}' 단지를 "
            f"실거래가/단지목록 어디에서도 찾지 못했습니다. 단지명을 정확히 입력했는지 확인하세요."
        )

    valuations = valuation.build_valuation(matched_trades)

    rents_raw = await molit_rent.get_rents(sigungu_cd, months=12)
    matched_rents = [
        r for r in rents_raw
        if molit_apt_list.fuzzy_name_matches(target_norm, molit_apt_list.normalize_name(r["apt_name"]))
    ]
    jeonse_summary = valuation.build_jeonse_summary(matched_rents)

    for v in valuations:
        best = None
        best_diff = None
        for j in jeonse_summary:
            diff = abs(j["avg_exclusive_area"] - v["avg_exclusive_area"])
            if diff <= 3 and (best_diff is None or diff < best_diff):
                best, best_diff = j, diff
        if best and v.get("fair_price_10k"):
            v["jeonse_median_10k"] = best["jeonse_median_10k"]
            v["jeonse_max_10k"] = best["jeonse_max_10k"]
            v["jeonse_latest_10k"] = best["jeonse_latest_10k"]
            v["jeonse_latest_date"] = best["jeonse_latest_date"]
            v["jeonse_sample_count_1y"] = best["jeonse_sample_count_1y"]
            v["jeonse_trades_1y"] = best["jeonse_trades_1y"]
            v["jeonse_ratio_pct"] = round(
                best["jeonse_median_10k"] / v["fair_price_10k"] * 100, 1
            )
        else:
            v["jeonse_median_10k"] = None
            v["jeonse_max_10k"] = None
            v["jeonse_trades_1y"] = []
            v["jeonse_latest_10k"] = None
            v["jeonse_latest_date"] = None
            v["jeonse_sample_count_1y"] = 0
            v["jeonse_ratio_pct"] = None

    # 지번(동+번지) 결정: 기본정보(basis_info) 주소가 가장 정확하고, 없으면 실거래가 내역 중
    # 가장 흔한 지번을 사용한다. 지오코딩과 건축물대장 조회 둘 다 이 지번을 공유한다.
    dong = None
    jibun_token = None
    if basis_info and basis_info.get("address"):
        jibun_token = _extract_jibun_token(basis_info["address"])
        dong = None  # kaptAddr 자체를 지오코딩 질의로 사용
    if not jibun_token:
        dong_jibun_pairs = [
            (t["dong"], t["jibun"]) for t in matched_trades if t.get("dong") and t.get("jibun")
        ]
        if dong_jibun_pairs:
            dong, jibun_token = Counter(dong_jibun_pairs).most_common(1)[0][0]

    # 지오코딩은 지번주소보다 도로명주소가 OSM/Nominatim에서 훨씬 정확하게 잡힌다
    # (실제 테스트 결과 지번주소는 동 중심으로 뭉뚱그려지는 반면, 도로명주소는 단지 내부
    # POI까지 정확히 매칭됨) — 그래서 도로명주소를 최우선으로 사용한다.
    geocode_result = None
    geocode_query = None
    if basis_info and basis_info.get("road_address"):
        geocode_query = basis_info["road_address"]
    elif basis_info and basis_info.get("address"):
        geocode_query = _address_up_to_jibun(basis_info["address"])
    elif dong and jibun_token:
        geocode_query = f"{sigungu['sido']} {sigungu['sigungu']} {dong} {jibun_token}"
    elif matched and matched.get("address"):
        geocode_query = matched["address"]

    if geocode_query:
        try:
            geocode_result = await geocode_client.geocode(geocode_query)
        except Exception:
            geocode_result = None

    # 건폐율/용적률 (건축물대장 표제부) — bjdCode(10자리, 시군구5+법정동5) + 지번 필요.
    building_info = None
    building_info_error = None
    bjd_code_10 = None
    if basis_info and basis_info.get("_raw", {}).get("bjdCode"):
        bjd_code_10 = basis_info["_raw"]["bjdCode"]
    elif matched and matched.get("bjd_code"):
        bjd_code_10 = matched["bjd_code"]

    # 대형/구축 단지는 여러 필지(지번)로 등록되어 있고 필지마다 총괄표제부 등록 상태가
    # 달라(예: 상계주공3단지 730-2는 98세대만 담긴 부실 레코드, 737번지가 2115세대짜리
    # 대표 레코드), 지번 후보를 여러 개 모아 그중 가장 대표성 있는 것을 고른다.
    jibun_candidates = []
    if jibun_token:
        jibun_candidates.append(jibun_token)
    trade_jibun_counts = Counter(t["jibun"] for t in matched_trades if t.get("jibun"))
    for jb, _ in trade_jibun_counts.most_common(4):
        if jb not in jibun_candidates:
            jibun_candidates.append(jb)

    building_title_list = []
    if bjd_code_10 and len(bjd_code_10) == 10 and jibun_candidates:
        try:
            building_info = await molit_building.get_building_recap_info_best(
                bjd_code_10[:5], bjd_code_10[5:10], jibun_candidates
            )
            bun, ji = _parse_bun_ji(jibun_candidates[0])
            building_title_list = await molit_building.get_building_title_list(
                bjd_code_10[:5], bjd_code_10[5:10], bun, ji
            )
        except molit_building.ApiNotRegisteredError as e:
            building_info_error = str(e)

    complex_key = make_complex_key(sigungu_cd, apt_name)

    return {
        "complex_key": complex_key,
        "sigungu": {"sido": sigungu["sido"], "sigungu": sigungu["sigungu"], "code": sigungu_cd},
        "matched_apt_name": apt_name,
        "official_info": matched,
        "basis_info": basis_info,
        "basis_info_error": basis_info_error,
        "building_info": building_info,
        "building_info_error": building_info_error,
        "building_title_list": building_title_list,
        "valuations": valuations,
        "trade_sample_total": len(matched_trades),
        "geocode": geocode_result,
        "geocode_query": geocode_query,
    }


def _address_up_to_jibun(addr: str) -> str:
    tok = _extract_jibun_token(addr)
    if not tok:
        return addr
    idx = addr.split().index(tok)
    return " ".join(addr.split()[: idx + 1])
