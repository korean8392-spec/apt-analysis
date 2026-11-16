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


async def get_with_retry(url: str, params: dict, timeout: float = 20, attempts: int = 3) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            # 연결(connect)은 8초만 기다리고 안 되면 바로 재시도한다 — 연결이 안 붙는 상황에서 오래
            # 기다리는 것보다 여러 번 다시 시도하는 편이 성공 확률이 높다.
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0)) as client:
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


async def gather_months_partial(
    fetch_month, lawd_cd: str, months: list[str], budget_sec: float = 60
) -> tuple[list[dict], list[str], Exception | None]:
    """gather_months와 같지만 일부 달이 실패하거나 늦어도 받은 만큼은 돌려준다.

    반환: (받은 달들의 거래 목록(달 순서), 못 받은 달 목록, 첫 오류). 시간 예산을 넘긴 달은
    취소하지 않고 뒤에서 계속 받아 캐시에 채우므로, 사용자가 다시 조회하면 더 빨리 완성된다."""
    sem = asyncio.Semaphore(MAX_CONCURRENT_MONTHS)

    async def one(ymd: str) -> list[dict]:
        async with sem:
            return await fetch_month(lawd_cd, ymd)

    tasks = {asyncio.ensure_future(one(ymd)): ymd for ymd in months}
    done, pending = await asyncio.wait(tasks, timeout=budget_sec)

    got: dict[str, list[dict]] = {}
    failed: list[str] = []
    first_error: Exception | None = None
    for task in done:
        try:
            got[tasks[task]] = task.result()
        except Exception as e:  # noqa: BLE001 - 달 하나의 실패는 모아서 알려준다
            failed.append(tasks[task])
            first_error = first_error or e
    for task in pending:
        failed.append(tasks[task])
        # 뒤에서 끝나는 작업의 예외가 "처리되지 않은 예외" 경고로 남지 않게 한다.
        task.add_done_callback(lambda f: f.cancelled() or f.exception())

    rows = [row for ymd in months if ymd in got for row in got[ymd]]
    return rows, sorted(failed), first_error
