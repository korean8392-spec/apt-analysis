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
                "dong": (row.get("as3") or "").strip(),
                "bjd_code": row.get("bjdCode"),
            }
        )
    db.cache_set(cache_key, normalized)
    return normalized


def normalize_name(name: str) -> str:
    """단지명 비교용 정규화: 공백/괄호/특수문자 제거 + 의미 없는 접미사 제거.

    "목련아파트"의 실제 등록명이 "목련"뿐인 경우처럼, "아파트/apt" 접미사가 있고
    없고의 표기 차이만으로 오매칭 방지 로직(fuzzy_name_matches의 짧은 이름 가드)에
    걸려 못 찾는 사례가 있어(실제 발견됨), 식별에 의미 없는 접미사는 미리 제거한다."""
    import re

    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"[^0-9A-Za-z가-힣]", "", name)
    name = name.strip().lower()
    stripped = re.sub(r"(아파트|apt)$", "", name)
    return stripped or name


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


def _strip_complex_number(name_norm: str) -> str:
    """'목련3단지우성' → '목련우성'처럼 'N단지'/'제N단지' 표기를 제거한다."""
    import re

    return re.sub(r"제?\d+단지", "", name_norm)


def _extract_signature(name_norm: str) -> tuple[str, str | None]:
    """단지 번호를 이름 내 위치와 분리해서 (핵심텍스트, 번호)로 뽑아낸다.

    'N단지' 표기가 아니라 단지 번호의 "위치"가 등록기관마다 다른 경우를 위한 것이다
    (실제 발견: 단지목록 "목동8단지" vs 실거래가 "목동신시가지8" — 숫자가 중간이 아니라
    끝에 붙고 "신시가지"라는 말이 끼어 있음). 마지막 숫자 그룹을 단지 번호로 보고
    나머지에서 신시가지/단지/제 같은 연결어를 지운 걸 핵심텍스트로 삼는다."""
    import re

    last_digit = None
    for m in re.finditer(r"\d+", name_norm):
        last_digit = m
    if not last_digit:
        return re.sub(r"(신시가지|단지|제)", "", name_norm), None
    number = last_digit.group(0)
    rest = name_norm[: last_digit.start()] + name_norm[last_digit.end() :]
    core = re.sub(r"(신시가지|단지|제)", "", rest)
    return core, number


def _strip_all_digits(name_norm: str) -> str:
    import re

    return re.sub(r"\d+", "", name_norm)


def _bare_name_matches(query_norm: str, candidate_norm: str) -> bool:
    """검색어에 번호가 전혀 없을 때만, 후보명에서 숫자를 다 지우고 비교한다.

    공식명이 "방화2-2 그린"처럼 사용자가 입력하지 않을 법한 동/블록 식별자를
    포함하는 경우를 위한 것이다(실제 발견: 검색어 "방화그린" vs 공식명
    "방화2-2 그린" — 정규화 후 "방화22그린"이 되어 기존 매칭 어느 것도 못 찾음).
    검색어 자체에 번호가 있으면(예: "창동주공17단지") 이 함수를 타지 않으므로,
    번호가 다른 동일 계열 단지(18/19단지 등)를 끌어오는 위험은 없다."""
    q_core, q_num = _extract_signature(query_norm)
    if q_num is not None or not query_norm:
        return False
    return _strip_all_digits(candidate_norm) == query_norm


def _signature_matches(query_norm: str, candidate_norm: str) -> bool:
    """핵심텍스트(단지번호 제외)와 단지번호가 둘 다 일치할 때만 True.

    _strip_complex_number 기반 매칭과 달리 번호를 지우지 않고 비교하므로,
    "창동주공17단지"와 "창동주공18단지"처럼 핵심텍스트는 같지만 번호가 다른
    별개 단지끼리는 매칭되지 않는다(실제로 발견된 오탐 — 번호를 지우는 매칭을
    후보 목록 검색에 썼더니 같은 계열의 다른 번호 단지가 전부 후보로 잘못
    끼어들었다)."""
    q_core, q_num = _extract_signature(query_norm)
    c_core, c_num = _extract_signature(candidate_norm)
    return bool(q_core and c_core and q_num and c_num and q_core == c_core and q_num == c_num)


def fuzzy_name_matches_relaxed(query_norm: str, candidate_norm: str) -> bool:
    """fuzzy_name_matches보다 한 단계 더 관대한 매칭 — 실거래가 데이터와 단지목록
    데이터의 단지 번호 표기 방식이 다른 실제 사례들을 보완한다. 이미 특정 단지가
    확정된 후 그 단지의 실거래 내역을 찾는 단계에서만 쓴다.

    단지번호를 뗀 뒤에는 포함관계가 아니라 "완전히 같은 문자열"일 때만 매칭한다 —
    포함관계를 허용하면 "목련우성"(번호 없음)이 "목련우성5"/"목련우성7"(다른 단지)의
    접두사가 되어 서로 다른 단지끼리 뭉쳐버린다(실제로 발견된 오탐)."""
    if fuzzy_name_matches(query_norm, candidate_norm):
        return True

    # 표기1: 'N단지'가 아예 빠진 경우 (예: 공식명 "목련3단지우성" vs 실거래가 "목련우성")
    q2, c2 = _strip_complex_number(query_norm), _strip_complex_number(candidate_norm)
    if (q2 != query_norm or c2 != candidate_norm) and q2 and c2 and q2 == c2:
        return True

    # 표기2: 번호는 있지만 위치/연결어가 다른 경우 (예: "목동8단지" vs "목동신시가지8") —
    # 핵심텍스트와 번호가 둘 다 일치할 때만 인정한다(번호가 다르면 다른 단지이므로 제외).
    if _signature_matches(query_norm, candidate_norm):
        return True

    # 표기3: 실거래가가 지역명 없이 사업주체명("주공")+번호만 쓰는 경우(예: 공식명
    # "등촌3단지주공아파트"=지역명+번호+주공 vs 실거래가 "주공3"=주공+번호만, 지역명 생략).
    # 번호가 같고 한쪽 핵심텍스트가 지역명 없이 "주공"뿐이며 다른 쪽 핵심텍스트가 그 "주공"을
    # 포함하면 같은 단지로 본다 — 이 경우 전국에 흔한 "주공N" 표기가 다른 동네의 별개 단지와
    # 겹칠 위험이 있으므로, 호출부(complex_search.py)에서 반드시 법정동 일치 여부로 한 번 더
    # 걸러야 한다.
    q_core, q_num = _extract_signature(query_norm)
    c_core, c_num = _extract_signature(candidate_norm)
    if q_num and c_num and q_num == c_num:
        if c_core == "주공" and "주공" in q_core:
            return True
        if q_core == "주공" and "주공" in c_core:
            return True

    # 표기4: 어느 한쪽에는 번호가 아예 없고, 번호를 전부 지운 핵심텍스트는 서로 같은
    # 경우(예: 공식명 "방화2-2 그린"=지역명+블록번호+이름 vs 실거래가 "방화그린"=번호
    # 생략 — "N단지"도 아니고 번호 위치도 특정되지 않아 앞의 표기들로는 못 잡음). 이미
    # 특정 단지가 확정된 뒤에만 쓰이므로(find_candidates가 아니라 여기, 실거래가 매칭
    # 단계) 오매칭 위험이 낮다.
    if q_num is None or c_num is None:
        q_bare, c_bare = _strip_all_digits(query_norm), _strip_all_digits(candidate_norm)
        if q_bare and c_bare and q_bare == c_bare:
            return True

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
    if not exact and not partial:
        # 단지목록 공식명은 "등촌3단지주공아파트"처럼 번호가 "주공" 앞에 오는 등, 검색어의
        # 자연스러운 어순("등촌주공3단지")과 순서가 달라 기본 매칭이 실패하는 경우가 있다
        # (실제 발견) — 이때만 완화된 매칭으로 재시도한다. 단, fuzzy_name_matches_relaxed의
        # 번호-제거 비교(표기1)는 여기서 쓰면 안 된다 — "창동주공17단지"를 검색했는데
        # 단지목록에 17단지가 아예 없는 경우, 번호를 지우고 비교하면 핵심텍스트가 같은
        # 18/19/4/3/2단지 등 완전히 다른 번호의 단지가 전부 후보로 잘못 끼어든다(실제로
        # 발견된 오탐). 번호까지 정확히 일치해야 하는 _signature_matches만 쓴다.
        partial = [
            a for a in apt_list
            if _signature_matches(target, normalize_name(a["name"]))
        ]
    if not exact and not partial:
        # 방화그린 사례: 검색어에 번호가 전혀 없는데 공식명에만 동/블록 식별자
        # 번호가 붙어있는 경우(예: "방화2-2 그린"). 검색어에 번호가 없을 때만
        # 시도하므로 번호로 구분되는 동일 계열 단지를 잘못 끌어올 위험이 없다.
        partial = [
            a for a in apt_list
            if _bare_name_matches(target, normalize_name(a["name"]))
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
