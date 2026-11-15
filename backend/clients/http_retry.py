"""국토부 공공데이터 API 호출용 재시도/동시 조회 도우미.

공공데이터 API는 가끔 ConnectTimeout/ReadTimeout이나 5xx로 일시 실패하고, 바로 다시 호출하면
대부분 성공한다(실제로 확인됨). 시군구 하나의 36개월치를 한 달씩 받는 구조라 한 번의 일시
실패가 전체 조회 실패로 이어지던 것을 막는다.
"""

import asyncio
import httpx

TRANSIENT_ERRORS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)
# 한 번에 너무 많은 요청을 보내 공공데이터 쪽에서 막히지 않도록 동시 조회 수를 제한한다.
MAX_CONCURRENT_MONTHS = 5


async def get_with_retry(url: str, params: dict, timeout: float = 15, attempts: int = 3) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, params=params)
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(f"server error {resp.status_code}", request=resp.request, response=resp)
            return resp
        except TRANSIENT_ERRORS + (httpx.HTTPStatusError,) as e:
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                raise
            last_error = e
            if attempt < attempts - 1:
                await asyncio.sleep(0.7 * (attempt + 1))
    assert last_error is not None
    raise last_error


async def gather_months(fetch_month, lawd_cd: str, months: list[str]) -> list[dict]:
    """여러 달을 동시에(최대 MAX_CONCURRENT_MONTHS개) 받아 달 순서대로 이어 붙인다."""
    sem = asyncio.Semaphore(MAX_CONCURRENT_MONTHS)

    async def one(ymd: str) -> list[dict]:
        async with sem:
            return await fetch_month(lawd_cd, ymd)

    per_month = await asyncio.gather(*(one(ymd) for ymd in months))
    return [row for rows in per_month for row in rows]
