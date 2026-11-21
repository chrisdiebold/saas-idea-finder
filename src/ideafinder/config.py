"""Project configuration from `config/*.yaml`, validated on load.

These files describe *what* the system does (Sources, scoring, model routing) and are kept in git.
Machine-specific values such as the database address live in `settings.py` instead.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- sources.yaml ---------------------------------------------------------------------------


class SourceConfig(_Strict):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    phase: Literal[1, 2, 3]
    enabled: bool = True
    rate_limit_per_minute: int = Field(gt=0)
    # The Watch list: Communities collected from regularly (subreddit names, Jira project keys,
    # Stack Exchange site names, WordPress plugin slugs, ...). Meaning depends on the Source.
    communities: list[str] = []
    # Source-specific settings, interpreted by that Source's collector.
    options: dict[str, Any] = {}


class QueryConfig(_Strict):
    """Pain-phrase queries, run by every Source that supports search."""

    request: list[str]
    payment: list[str]
    workaround: list[str]
    complaint: list[str]
    question: list[str]

    def all(self) -> list[str]:
        return [*self.request, *self.payment, *self.workaround, *self.complaint, *self.question]


class SourcesConfig(_Strict):
    language: Literal["en"] = "en"
    backfill_months: int = Field(ge=0, le=60)
    queries: QueryConfig
    sources: list[SourceConfig]

    @field_validator("sources")
    @classmethod
    def _unique_keys(cls, sources: list[SourceConfig]) -> list[SourceConfig]:
        keys = [s.key for s in sources]
        duplicates = {k for k in keys if keys.count(k) > 1}
        if duplicates:
            raise ValueError(f"duplicate source keys: {sorted(duplicates)}")
        return sources

    def enabled(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


# --- scoring.yaml ---------------------------------------------------------------------------


class ScoringWeights(_Strict):
    volume: float = Field(ge=0)
    spread: float = Field(ge=0)
    intensity: float = Field(ge=0)
    payment: float = Field(ge=0)
    momentum: float = Field(ge=0)
    competitive_gap: float = Field(ge=0)
    fit: float = Field(ge=0)
    solo_buildable: float = Field(ge=0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> "ScoringWeights":
        total = sum(self.model_dump().values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"scoring weights must sum to 1.0, got {total:.3f}")
        return self


class PublishThreshold(_Strict):
    min_authors: int = Field(ge=1)
    min_communities: int = Field(ge=1)
    require_pain_or_request: bool


class ResurfaceRule(_Strict):
    momentum_multiplier: float = Field(gt=1)
    on_new_community: bool


class StaleRule(_Strict):
    signal_growth_pct: int = Field(gt=0)
    on_new_community: bool


class ValidationRules(_Strict):
    time_limit_days: int = Field(gt=0)
    active_limit: int = Field(gt=0)


class ScoringConfig(_Strict):
    weights: ScoringWeights
    thread_cap: int = Field(gt=0, description="Max Signals one Thread contributes to an Idea")
    publish: PublishThreshold
    resurface: ResurfaceRule
    stale: StaleRule
    validation: ValidationRules


# --- models.yaml ----------------------------------------------------------------------------

ProviderName = Literal["lmstudio", "anthropic", "manual"]
TaskName = Literal[
    "extract",
    "embed",
    "name_idea",
    "fit_check",
    "propose_decision",
    "summary",
    "industry_codes",
    "lead_opener",
    "validation_kit",
    "briefing",
]


class ProviderConfig(_Strict):
    base_url: str | None = None


class TaskRoute(_Strict):
    provider: ProviderName
    model: str | None = None  # None until chosen (the local model is picked by the M2 eval)


class ModelsConfig(_Strict):
    providers: dict[ProviderName, ProviderConfig]
    tasks: dict[TaskName, TaskRoute]

    @model_validator(mode="after")
    def _routes_are_complete(self) -> "ModelsConfig":
        missing = set(TaskName.__args__) - set(self.tasks)
        if missing:
            raise ValueError(f"no route for tasks: {sorted(missing)}")
        unknown = {r.provider for r in self.tasks.values()} - set(self.providers)
        if unknown:
            raise ValueError(f"tasks use providers that aren't configured: {sorted(unknown)}")
        return self


# --- loading --------------------------------------------------------------------------------


class Config(_Strict):
    sources: SourcesConfig
    scoring: ScoringConfig
    models: ModelsConfig


class ConfigError(Exception):
    """A config file is missing or invalid. The message names the file."""


def _load_yaml[T: BaseModel](path: Path, model: type[T]) -> T:
    if not path.exists():
        raise ConfigError(f"{path}: file not found")
    try:
        data = yaml.safe_load(path.read_text()) or {}
        return model.model_validate(data)
    except (yaml.YAMLError, ValueError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def load_config(config_dir: Path) -> Config:
    return Config(
        sources=_load_yaml(config_dir / "sources.yaml", SourcesConfig),
        scoring=_load_yaml(config_dir / "scoring.yaml", ScoringConfig),
        models=_load_yaml(config_dir / "models.yaml", ModelsConfig),
    )
