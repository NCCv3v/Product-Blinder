"""Frozen, validated evaluation contracts. No credentials are part of these models."""

import hashlib
import json
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Case(Contract):
    id: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=40000)
    expected: str | None = None
    evidence: list[str] = Field(default_factory=list)
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    category: Literal["standard", "typo", "paraphrase", "negation", "fairness", "injection"] = "standard"
    pair: str | None = None
    max_latency_ms: float = Field(default=5000.0, gt=0)
    rubric: str | None = None
    rubric_source: Literal["human", "ai-proposed"] = "human"
    source: Literal["authored", "agent-generated"] = "authored"
    agent: str | None = None
    check: str | None = None

    @model_validator(mode="after")
    def valid_schema(self):
        if self.schema_ is not None:
            import jsonschema

            try:
                jsonschema.validators.validator_for(self.schema_).check_schema(self.schema_)
            except jsonschema.SchemaError as exc:
                raise ValueError("Invalid JSON Schema in case") from exc
        return self


class Suite(Contract):
    name: str
    version: str = "1"
    description: str = ""
    cases: list[Case] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("Case IDs must be unique")
        for c in self.cases:
            if c.category == "fairness" and not c.pair:
                raise ValueError("Fairness cases need a counterfactual pair label")
        return self

    @property
    def digest(self):
        return fingerprint(self.model_dump(by_alias=True))


def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_suite(text: str) -> Suite:
    if len(text.encode()) > 2_000_000:
        raise ValueError("Suite exceeds 2 MB limit")
    # safe_load does NOT import Python objects or execute workflow files.
    return Suite.model_validate(yaml.safe_load(text))


class TargetConfig(Contract):
    kind: Literal["fixture", "http", "program"] = "fixture"
    fixture: Literal["stable", "degraded"] = "stable"
    url: str = ""
    input_path: str = Field(default="input", min_length=1)
    output_path: str = Field(default="output", min_length=1)
    body: dict[str, Any] = Field(default_factory=dict)
    auth_header: str = Field(default="Authorization", min_length=1)
    auth_prefix: str = "Bearer "
    command: list[str] = Field(default_factory=list)
    model: str = "unspecified"
    prompt_version: str = "unspecified"
    revision: str = "unspecified"


class RunConfig(Contract):
    timeout_s: float = Field(default=20, ge=0.1, le=300)
    concurrency: int = Field(default=4, ge=1, le=16)
    max_calls: int = Field(default=200, ge=1, le=10000)
    max_judge_calls: int = Field(default=100, ge=0, le=2000)
    retry: int = Field(default=1, ge=0, le=2)
    use_judge: bool = False
    judge_base_url: str = "https://api.openai.com/v1"
    judge_model: str = ""
    judge_prompt_version: str = "pb-judge-v1"
    # Reserve conservative caller-declared upper bounds; not a provider billing guarantee.
    budget_usd: float = Field(default=1, ge=0)
    target_call_cap_usd: float | None = Field(default=None, ge=0)
    judge_call_cap_usd: float | None = Field(default=None, ge=0)
    max_output_tokens: int = Field(default=600, ge=50, le=4000)
    regression_threshold: float = Field(default=0.05, gt=0, le=1)
    min_pairs: int = Field(default=10, ge=2, le=1000)
    agent_count: int = Field(default=8, ge=5, le=8)
    use_agent_planner: bool = False
    max_generated_cases: int = Field(default=4, ge=0, le=8)

    @property
    def scoring_digest(self):
        return fingerprint(
            {
                "engine": "pb-rules-v1",
                "judge": self.use_judge,
                "base": self.judge_base_url if self.use_judge else None,
                "model": self.judge_model if self.use_judge else None,
                "prompt": self.judge_prompt_version,
                "max_tokens": self.max_output_tokens,
            }
        )
