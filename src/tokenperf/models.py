"""Validated public models for benchmark workloads and measurements."""

from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    content: str


class BenchmarkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str
    model: str = Field(min_length=1)
    api_key_env: str | None = "OPENAI_API_KEY"
    messages: list[Message] = Field(
        default_factory=lambda: [Message(role="user", content="Say hello briefly.")], min_length=1
    )
    inputs: list[list[Message]] | None = None
    generation: dict[str, Any] = Field(default_factory=lambda: {"max_tokens": 128})
    stream: bool = True
    requests: int = Field(default=10, gt=0, strict=True)
    concurrency: int | list[int] = 1
    warmup: int = Field(default=1, ge=0, strict=True)
    timeout: float = Field(default=120, gt=0, allow_inf_nan=False)
    seed: int = Field(default=0, strict=True)

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "endpoint must be an HTTP(S) URL without credentials, query or fragment"
            )
        _ = url.port  # Reject invalid or out-of-range ports during validation.
        return value.rstrip("/")

    @field_validator("concurrency", mode="before")
    @classmethod
    def valid_concurrency(cls, value: Any) -> Any:
        values = value if isinstance(value, list) else [value]
        if not values or any(type(item) is not int or item <= 0 for item in values):
            raise ValueError("concurrency must contain positive integers")
        if len(set(values)) != len(values):
            raise ValueError("concurrency values must be unique")
        return value

    @field_validator("inputs")
    @classmethod
    def valid_inputs(cls, value: Any) -> Any:
        if value is not None and (not value or any(not item for item in value)):
            raise ValueError("inputs and every input must be nonempty")
        return value

    @model_validator(mode="after")
    def valid_generation(self) -> "BenchmarkConfig":
        if {"model", "messages", "stream", "stream_options"} & self.generation.keys():
            raise ValueError("generation contains reserved request fields")
        if "seed" in self.generation and (
            type(self.generation["seed"]) is not int or self.generation["seed"] != self.seed
        ):
            raise ValueError("generation seed conflicts with benchmark seed")
        if "n" in self.generation and (
            type(self.generation["n"]) is not int or self.generation["n"] != 1
        ):
            raise ValueError("only n=1 text completions are supported")
        if {
            "tools",
            "tool_choice",
            "functions",
            "function_call",
            "modalities",
            "audio",
        } & self.generation.keys():
            raise ValueError("only text generation is supported")
        return self


class RequestRecord(BaseModel):
    request_id: int
    input_id: str
    warmup: bool = False
    success: bool = False
    error: str | None = None
    status_code: int | None = None
    queue_seconds: float = 0
    latency_seconds: float = 0
    first_content_seconds: float | None = None
    chunk_intervals_seconds: list[float] = Field(default_factory=list)
    output_tokens: int | None = None
    generation_tokens_per_second: float | None = None
    output_tokens_per_second: float | None = None


class ConditionResult(BaseModel):
    concurrency: int
    requests: list[RequestRecord] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class BenchmarkResult(BaseModel):
    schema_version: str = "1"
    tool_version: str = "0.0.0b0"
    run_id: str
    started_at: str
    status: str = "completed"
    config: dict[str, Any]
    conditions: list[ConditionResult] = Field(default_factory=list)
    output_dir: str
