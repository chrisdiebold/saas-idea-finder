import httpx2
import pytest

from ideafinder.collectors.base import HttpSource


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("ideafinder.collectors.base.asyncio.sleep", fake_sleep)
    return slept


def _client(responses):
    calls = iter(responses)
    return httpx2.AsyncClient(transport=httpx2.MockTransport(lambda request: next(calls)))


async def test_retries_server_errors_then_succeeds(no_sleep):
    async with _client([httpx2.Response(503), httpx2.Response(200, json={"ok": 1})]) as c:
        assert await HttpSource(c, 600).get_json("https://x.test/") == {"ok": 1}
    assert len(no_sleep) == 1


async def test_honours_retry_after(no_sleep):
    responses = [httpx2.Response(429, headers={"Retry-After": "7"}), httpx2.Response(200, json={})]
    async with _client(responses) as c:
        await HttpSource(c, 600).get("https://x.test/")
    assert no_sleep == [7.0]


async def test_gives_up_after_max_attempts():
    async with _client([httpx2.Response(500)] * 3) as c:
        with pytest.raises(httpx2.HTTPStatusError):
            await HttpSource(c, 600, max_attempts=3).get("https://x.test/")


async def test_client_errors_are_not_retried(no_sleep):
    async with _client([httpx2.Response(404)]) as c:
        with pytest.raises(httpx2.HTTPStatusError):
            await HttpSource(c, 600).get("https://x.test/")
    assert no_sleep == []
