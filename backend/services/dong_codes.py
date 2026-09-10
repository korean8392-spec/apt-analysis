import csv
from functools import lru_cache

from config import LEGAL_DONG_CSV


@lru_cache
def load_dong_codes() -> list[dict]:
    with open(LEGAL_DONG_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def search_sigungu(keyword: str) -> list[dict]:
    """시/군/구 이름 일부로 검색 (예: '분당', '강남', '수원 영통')."""
    keyword = keyword.replace(" ", "")
    rows = load_dong_codes()
    results = []
    for row in rows:
        haystack = (row["sido"] + row["sigungu"]).replace(" ", "")
        if keyword in haystack:
            results.append(row)
    return results


def resolve_sigungu(keyword: str) -> dict:
    """검색어가 시군구 하나로 확정되지 않으면(0개/여러개) 에러를 낸다."""
    candidates = search_sigungu(keyword)
    if not candidates:
        raise ValueError(f"'{keyword}'에 해당하는 서울/경기 시군구를 찾지 못했습니다.")
    if len(candidates) > 1:
        options = ", ".join(f"{r['sido']} {r['sigungu']}" for r in candidates)
        raise ValueError(f"시군구가 여러 개 검색되었습니다. 더 구체적으로 입력하세요: {options}")
    return candidates[0]
