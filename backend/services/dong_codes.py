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
