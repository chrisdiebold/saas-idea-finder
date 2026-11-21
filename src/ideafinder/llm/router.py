"""One interface for every model task; config decides which provider and model serve each."""

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx2

from ideafinder.config import ModelsConfig, TaskName

REQUEST_TIMEOUT = 600.0  # long prompts on a local model can take minutes


class ModelUnavailable(Exception):
    """The provider can't serve the request (model missing, bad output, not configured)."""


class ServerUnreachable(ModelUnavailable):
    """The model server isn't running. Nothing will work until it is, so batches should stop."""


@dataclass(frozen=True)
class JsonResult:
    data: dict[str, Any]
    model: str
    seconds: float
    input_tokens: int | None
    output_tokens: int | None


class LLM:
    def __init__(self, config: ModelsConfig, transport: httpx2.AsyncBaseTransport | None = None):
        self.config = config
        self._client = httpx2.AsyncClient(timeout=REQUEST_TIMEOUT, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "LLM":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    def model_for(self, task: TaskName, override: str | None = None) -> str:
        model = override or self.config.tasks[task].model
        if not model:
            raise ModelUnavailable(f"no model configured for task '{task}' in config/models.yaml")
        return model

    def _openai_base(self, task: TaskName) -> str:
        route = self.config.tasks[task]
        if route.provider != "lmstudio":
            raise ModelUnavailable(
                f"task '{task}' is routed to '{route.provider}', which isn't implemented yet"
            )
        base = self.config.providers["lmstudio"].base_url
        if not base:
            raise ModelUnavailable("providers.lmstudio.base_url is not set")
        return base.rstrip("/")

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(url, json=payload)
        except httpx2.TransportError as exc:
            raise ServerUnreachable(
                f"can't reach the model server at {url} ({exc}). Start it with: lms server start"
            ) from exc
        if response.status_code >= 400:
            raise ModelUnavailable(
                f"model server returned {response.status_code}: {response.text[:300]}"
            )
        return response.json()

    async def json(
        self,
        task: TaskName,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> JsonResult:
        """Ask for a JSON object matching `schema` (enforced by the server's structured output)."""
        base = self._openai_base(task)  # first: an unsupported provider is the clearer error
        model_id = self.model_for(task, model)
        started = time.perf_counter()
        body = await self._post(
            f"{base}/chat/completions",
            {
                "model": model_id,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "max_tokens": max_tokens,
                # Thinking models (e.g. Qwen3.6) otherwise have LM Studio put the whole
                # schema-constrained answer in `reasoning_content`, leaving `content` empty.
                "reasoning_effort": "none",
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": task, "strict": True, "schema": schema},
                },
            },
        )
        seconds = time.perf_counter() - started
        choice = body["choices"][0]
        content = choice["message"].get("content") or ""
        if choice.get("finish_reason") == "length":
            raise ModelUnavailable(f"{model_id} ran out of tokens ({max_tokens}) before finishing")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ModelUnavailable(f"{model_id} returned invalid JSON: {content[:200]!r}") from exc
        usage = body.get("usage") or {}
        return JsonResult(
            data=data,
            model=model_id,
            seconds=seconds,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        body = await self._post(
            f"{self._openai_base('embed')}/embeddings",
            {"model": self.model_for("embed", model), "input": texts},
        )
        return [row["embedding"] for row in sorted(body["data"], key=lambda r: r["index"])]
