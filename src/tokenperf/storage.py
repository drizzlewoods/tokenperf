"""Privacy-preserving artifact serialization."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote, quote_plus

from .models import BenchmarkConfig, BenchmarkResult, RequestRecord


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def sanitize_config(config: BenchmarkConfig, key: str | None) -> dict:
    inputs = config.inputs or [config.messages]
    ids = [fingerprint([m.model_dump() for m in item]) for item in inputs]
    generation = {**config.generation, "seed": config.seed}
    workload = {
        "input_ids": ids,
        "generation_fingerprint": fingerprint(generation),
        "requests": config.requests,
        "warmup": config.warmup,
        "seed": config.seed,
        "stream": config.stream,
        "timeout": config.timeout,
    }
    result = {
        **workload,
        "endpoint": config.endpoint,
        "model": config.model,
        "concurrency": config.concurrency,
        "comparison_fingerprint": fingerprint(workload),
    }
    return redact(result, key)


def redact(value: object, key: str | None) -> object:
    if isinstance(value, str):
        if key:
            for variant in sorted(
                {key, quote(key, safe=""), quote_plus(key)}, key=len, reverse=True
            ):
                value = value.replace(variant, "[REDACTED]")
                if "%" in variant:
                    pattern = "".join(
                        f"(?i:{re.escape(part)})"
                        if re.fullmatch(r"%[0-9A-Fa-f]{2}", part)
                        else re.escape(part)
                        for part in re.split(r"(%[0-9A-Fa-f]{2})", variant)
                    )
                    value = re.sub(pattern, "[REDACTED]", value)
        return value
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    if isinstance(value, dict):
        return {str(redact(k, key)): redact(v, key) for k, v in value.items()}
    return value


class ArtifactWriter:
    def __init__(self, path: Path, key: str | None, config: dict) -> None:
        path.mkdir(parents=True, exist_ok=False)
        self.path = path
        self.key = key
        self.write_json("config.json", config)
        self.records = (path / "records.jsonl").open("x", encoding="utf-8")

    def write_json(self, name: str, value: object) -> None:
        (self.path / name).write_text(
            json.dumps(redact(value, self.key), ensure_ascii=False, indent=2, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )

    def record(self, concurrency: int, record: RequestRecord) -> None:
        data = {"concurrency": concurrency, **record.model_dump()}
        self.records.write(
            json.dumps(redact(data, self.key), ensure_ascii=False, allow_nan=False) + "\n"
        )
        self.records.flush()

    def finish(self, result: BenchmarkResult) -> None:
        try:
            self.write_json("summary.json", result.model_dump())
        finally:
            self.records.close()
