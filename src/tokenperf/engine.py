"""Bounded async workers and end-to-end request deadlines."""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

import httpx

from .metrics import summarize
from .models import BenchmarkConfig, BenchmarkResult, ConditionResult, RequestRecord
from .protocol import ProtocolError, parse_nonstream, parse_stream
from .storage import ArtifactWriter, sanitize_config


async def run_benchmark(
    config: BenchmarkConfig, output_dir: Path | str | None = None
) -> BenchmarkResult:
    key = os.environ.get(config.api_key_env) if config.api_key_env else None
    if config.api_key_env and not key:
        raise ValueError("configured API key environment variable is missing or empty")
    run_id = uuid4().hex
    path = Path(output_dir) if output_dir is not None else Path("tokenperf-results") / run_id
    metadata = sanitize_config(config, key)
    writer = ArtifactWriter(path, key, metadata)
    result = BenchmarkResult(
        run_id=run_id,
        started_at=datetime.now(UTC).isoformat(),
        config=metadata,
        output_dir=str(path.resolve()),
    )
    endpoint = (
        config.endpoint
        if config.endpoint.endswith("/chat/completions")
        else config.endpoint + "/chat/completions"
    )
    inputs = config.inputs or [config.messages]
    input_ids = metadata["input_ids"]
    levels = config.concurrency if isinstance(config.concurrency, list) else [config.concurrency]
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    active: ConditionResult | None = None
    measured_start: float | None = None

    async def perform(
        client: httpx.AsyncClient, index: int, warmup: bool, queued: float
    ) -> RequestRecord:
        started = monotonic()
        item = index % len(inputs)
        record = RequestRecord(
            request_id=index,
            input_id=input_ids[item],
            warmup=warmup,
            queue_seconds=started - queued,
        )
        payload = {
            **config.generation,
            "seed": config.seed,
            "model": config.model,
            "messages": [m.model_dump() for m in inputs[item]],
            "stream": config.stream,
        }
        if config.stream:
            payload["stream_options"] = {"include_usage": True}
        try:
            async with asyncio.timeout(config.timeout):
                async with client.stream("POST", endpoint, json=payload) as response:
                    record.status_code = response.status_code
                    if not 200 <= response.status_code < 300:
                        record.error = f"http_{response.status_code}"
                        return record
                    completion = await (
                        parse_stream(response) if config.stream else parse_nonstream(response)
                    )
                    record.output_tokens = completion.tokens
                    if completion.times:
                        record.first_content_seconds = completion.times[0] - started
                        record.chunk_intervals_seconds = [
                            b - a
                            for a, b in zip(completion.times, completion.times[1:], strict=False)
                        ]
            record.success = True
        except TimeoutError:
            record.error = "timeout"
        except ProtocolError as exc:
            record.error = str(exc)
        except httpx.HTTPError:
            record.error = "transport_error"
        except (ValueError, TypeError, UnicodeError):
            record.error = "invalid_request_or_response"
        finally:
            record.latency_seconds = monotonic() - started
            if record.success and completion.rate_safe and completion.tokens is not None:
                record.output_tokens_per_second = completion.tokens / record.latency_seconds
        return record

    async def phase(
        client: httpx.AsyncClient, condition: ConditionResult, count: int, warmup: bool
    ) -> None:
        queued = monotonic()
        indices = iter(range(count))

        async def worker() -> None:
            for index in indices:
                record = await perform(client, index, warmup, queued)
                condition.requests.append(record)
                writer.record(condition.concurrency, record)

        workers = [asyncio.create_task(worker()) for _ in range(min(condition.concurrency, count))]
        try:
            await asyncio.gather(*workers)
        finally:
            for worker_task in workers:
                if not worker_task.done():
                    worker_task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    try:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=None,
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=max(levels), max_keepalive_connections=max(levels)),
        ) as client:
            for concurrency in levels:
                active = ConditionResult(concurrency=concurrency)
                result.conditions.append(active)
                measured_start = None
                await phase(client, active, config.warmup, True)
                measured_start = monotonic()
                await phase(client, active, config.requests, False)
                active.summary = summarize(active.requests, monotonic() - measured_start)
                measured_start = None
    except asyncio.CancelledError:
        result.status = "cancelled"
        raise
    except Exception:
        result.status = "failed"
        raise
    finally:
        if active is not None and not active.summary:
            active.summary = summarize(
                active.requests, monotonic() - measured_start if measured_start is not None else 0
            )
        writer.finish(result)
    return result
