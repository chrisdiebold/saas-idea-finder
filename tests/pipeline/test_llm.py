import json

import httpx2
import pytest

from ideafinder.config import load_config
from ideafinder.llm import LLM, ModelUnavailable, ServerUnreachable

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


@pytest.fixture
def models(repo_config_dir):
    return load_config(repo_config_dir).models


def transport(handler):
    return httpx2.MockTransport(handler)


async def test_json_sends_schema_and_parses_answer(models):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 4},
            },
        )

    async with LLM(models, transport(handler)) as llm:
        result = await llm.json("extract", "sys", "user", SCHEMA)
    assert result.data == {"ok": True} and result.output_tokens == 4
    assert seen["model"] == models.tasks["extract"].model
    assert seen["response_format"]["json_schema"]["schema"] == SCHEMA
    assert seen["temperature"] == 0
    assert seen["reasoning_effort"] == "none"


async def test_truncated_or_invalid_output_is_an_error(models):
    def truncated(request):
        return httpx2.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": tr'}, "finish_reason": "length"}]},
        )

    async with LLM(models, transport(truncated)) as llm:
        with pytest.raises(ModelUnavailable, match="ran out of tokens"):
            await llm.json("extract", "s", "u", SCHEMA)


async def test_server_down_is_reported_as_unreachable(models):
    def down(request):
        raise httpx2.ConnectError("refused")

    async with LLM(models, transport(down)) as llm:
        with pytest.raises(ServerUnreachable, match="lms server start"):
            await llm.json("extract", "s", "u", SCHEMA)


async def test_unimplemented_provider_is_explained(models):
    async with LLM(models) as llm:
        with pytest.raises(ModelUnavailable, match="manual"):
            await llm.json("briefing", "s", "u", SCHEMA)


async def test_embeddings_keep_input_order(models):
    def handler(request):
        return httpx2.Response(
            200, json={"data": [{"index": 1, "embedding": [0.2]}, {"index": 0, "embedding": [0.1]}]}
        )

    async with LLM(models, transport(handler)) as llm:
        assert await llm.embed(["a", "b"]) == [[0.1], [0.2]]
