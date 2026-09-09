import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from tokenperf.engine import run_benchmark
from tokenperf.metrics import summarize
from tokenperf.models import BenchmarkConfig, RequestRecord
from tokenperf.protocol import ProtocolError, parse_stream, usage_tokens


def sse(content="hello", usage=True, done=True):
    events = [
        {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": content}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    if usage:
        events.append({"choices": [], "usage": {"completion_tokens": 1}})
    body = ": comment\n\n" + "".join(
        "data: " + json.dumps(x, ensure_ascii=False) + "\n\n" for x in events
    )
    return (body + ("data: [DONE]\n\n" if done else "")).encode()


class Fragmented(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data

    async def __aiter__(self):
        for byte in self.data:
            yield bytes([byte])


@asynccontextmanager
async def server(body=None, status=200, delay=0):
    stats = {"active": 0, "peak": 0, "calls": 0, "payloads": []}
    tasks = set()
    stats["started"] = asyncio.Event()

    async def handle(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            stats["payloads"].append(json.loads(await reader.readexactly(length)))
            stats["active"] += 1
            stats["calls"] += 1
            if stats["calls"] >= 2:
                stats["started"].set()
            stats["peak"] = max(stats["peak"], stats["active"])
            await asyncio.sleep(delay)
            payload = sse() if body is None else body
            writer.write(
                (
                    f"HTTP/1.1 {status} Test\r\nContent-Type: text/event-stream\r\n"
                    f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
                ).encode()
                + payload
            )
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            stats["active"] -= 1
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    listener = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/v1", stats
    finally:
        listener.close()
        await listener.wait_closed()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def cfg(endpoint, **kwargs):
    return BenchmarkConfig(endpoint=endpoint, model="test", api_key_env=None, **kwargs)


@pytest.mark.asyncio
async def test_fragmented_unicode_and_strict_eof():
    response = httpx.Response(200, stream=Fragmented(sse("你好")))
    completion = await parse_stream(response)
    assert completion.has_content and completion.tokens == 1
    with pytest.raises(ProtocolError, match="unexpected_eof"):
        await parse_stream(httpx.Response(200, stream=Fragmented(sse(done=False))))


@pytest.mark.asyncio
async def test_workers_warmup_inputs_and_artifacts(tmp_path):
    async with server(delay=0.01) as (url, stats):
        config = cfg(
            url,
            requests=5,
            concurrency=2,
            warmup=2,
            inputs=[
                [{"role": "user", "content": "secret prompt A"}],
                [{"role": "user", "content": "secret prompt B"}],
            ],
        )
        result = await run_benchmark(config, tmp_path / "run")
    condition = result.conditions[0]
    assert stats["peak"] <= 2 and stats["peak"] == 2
    assert stats["calls"] == 7
    assert condition.summary["total"] == condition.summary["succeeded"] == 5
    assert len(condition.requests) == 7
    assert condition.summary["throughput_rps"] == 5 / condition.summary["elapsed_seconds"]
    measured = sorted((r for r in condition.requests if not r.warmup), key=lambda r: r.request_id)
    assert measured[0].input_id == measured[2].input_id != measured[1].input_id
    assert all(r.generation_tokens_per_second is None for r in measured)
    text = "".join(p.read_text() for p in (tmp_path / "run").iterdir())
    assert "secret prompt" not in text and "hello" not in text
    assert len((tmp_path / "run" / "records.jsonl").read_text().splitlines()) == 7
    with pytest.raises(FileExistsError):
        await run_benchmark(config, tmp_path / "run")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "status", "delay", "error"),
    [
        (b"server sensitive text", 429, 0, "http_429"),
        (sse(done=False), 200, 0, "unexpected_eof"),
        (None, 200, 0.1, "timeout"),
    ],
)
async def test_request_failures(tmp_path, body, status, delay, error):
    async with server(body, status, delay) as (url, _):
        result = await run_benchmark(cfg(url, requests=1, warmup=0, timeout=0.03), tmp_path / "run")
    record = result.conditions[0].requests[0]
    assert not record.success and record.error == error
    assert result.conditions[0].summary["latency_seconds"]["p50"] is None


@pytest.mark.asyncio
async def test_cancel_persists_and_stops_workers(tmp_path):
    async with server(delay=0.2) as (url, stats):
        task = asyncio.create_task(
            run_benchmark(cfg(url, requests=20, concurrency=2, warmup=0), tmp_path / "run")
        )
        await asyncio.wait_for(stats["started"].wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        count = stats["calls"]
        await asyncio.sleep(0.02)
        assert stats["calls"] == count
    summary = json.loads((tmp_path / "run" / "summary.json").read_text())
    assert summary["status"] == "cancelled"
    assert summary["conditions"][0]["summary"]["total"] == 0


@pytest.mark.asyncio
async def test_missing_usage(tmp_path):
    async with server(sse(usage=False)) as (url, _):
        result = await run_benchmark(cfg(url, requests=1, warmup=0), tmp_path / "run")
    assert result.conditions[0].requests[0].output_tokens is None


def test_metrics_exact_and_missing_values():
    records = [
        RequestRecord(request_id=i, input_id="x", success=True, latency_seconds=t)
        for i, t in enumerate([1, 2, 3])
    ]
    records += [
        RequestRecord(request_id=5, input_id="x", warmup=True, success=True, latency_seconds=100),
        RequestRecord(request_id=6, input_id="x", error="timeout", latency_seconds=9),
    ]
    summary = summarize(records, 6)
    assert summary["latency_seconds"] == {"p50": 2, "p95": 2.9, "p99": 2.98}
    assert summary["throughput_rps"] == 0.5 and summary["success_rate"] == 0.75
    assert summary["failed_latency_seconds"]["p50"] == 9
    assert summary["first_content_seconds"]["p50"] is None


def test_validation_and_token_safety():
    for change in [
        {"concurrency": True},
        {"concurrency": []},
        {"inputs": []},
        {"generation": {"stream": True}},
        {"generation": {"seed": 8}},
        {"endpoint": "https://user:pass@example.org/v1"},
    ]:
        with pytest.raises(ValueError):
            BenchmarkConfig.model_validate(
                {"endpoint": "http://localhost/v1", "model": "x", **change}
            )
    assert usage_tokens({"completion_tokens": True}) == (None, False)
    assert usage_tokens({"completion_tokens": 3}) == (3, False)
    assert usage_tokens(
        {"completion_tokens": 3, "completion_tokens_details": {"reasoning_tokens": 0}}
    ) == (3, True)
    assert usage_tokens(
        {"completion_tokens": 3, "completion_tokens_details": {"reasoning_tokens": 1}}
    ) == (3, False)


@pytest.mark.asyncio
async def test_key_redaction_and_missing_env(tmp_path, monkeypatch):
    key = "Secret/key+value"
    monkeypatch.setenv("TOKENPERF_TEST_KEY", key)
    async with server(status=429) as (url, _):
        config = BenchmarkConfig(
            endpoint=url + "/Secret%2Fkey%2Bvalue",
            model=key,
            api_key_env="TOKENPERF_TEST_KEY",
            requests=1,
            warmup=0,
        )
        await run_benchmark(config, tmp_path / "run")
    persisted = "".join(p.read_text() for p in (tmp_path / "run").iterdir())
    assert key not in persisted and "Secret%2Fkey%2Bvalue" not in persisted
    monkeypatch.delenv("TOKENPERF_TEST_KEY")
    with pytest.raises(ValueError, match="missing or empty"):
        await run_benchmark(config, tmp_path / "missing")
    assert not (tmp_path / "missing").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["tool_calls", "content_filter", None])
async def test_nontext_and_missing_finish(finish):
    body = (
        "data: "
        + json.dumps({"choices": [{"delta": {"content": "x"}, "finish_reason": finish}]})
        + "\n\ndata: [DONE]\n\n"
    ).encode()
    with pytest.raises(ProtocolError):
        await parse_stream(httpx.Response(200, stream=Fragmented(body)))


@pytest.mark.asyncio
async def test_multiline_and_usage_only_terminal_chunk():
    body = (
        b'data: {"choices":\ndata: [{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
        b'data: {"choices":[],"usage":{"completion_tokens":3}}\n\ndata: [DONE]\n\n'
    )
    result = await parse_stream(httpx.Response(200, stream=Fragmented(body)))
    assert result.tokens == 3 and len(result.times) == 1


@pytest.mark.asyncio
async def test_nonstream_uses_total_latency_not_first_content(tmp_path):
    body = json.dumps(
        {
            "choices": [{"message": {"content": "private response"}, "finish_reason": "length"}],
            "usage": {"completion_tokens": 4, "completion_tokens_details": {"reasoning_tokens": 0}},
        }
    ).encode()
    async with server(body) as (url, _):
        result = await run_benchmark(cfg(url, stream=False, requests=1, warmup=0), tmp_path / "run")
    record = result.conditions[0].requests[0]
    assert record.success and record.first_content_seconds is None
    assert record.generation_tokens_per_second is None
    assert record.output_tokens_per_second == 4 / record.latency_seconds


def test_comparison_fingerprint_excludes_target():
    from tokenperf.storage import sanitize_config

    a = sanitize_config(cfg("http://one/v1", concurrency=1), None)
    b = sanitize_config(
        BenchmarkConfig(endpoint="http://two/v1", model="different", concurrency=[2, 4]), None
    )
    assert a["comparison_fingerprint"] == b["comparison_fingerprint"]
    c = sanitize_config(cfg("http://one/v1", seed=1), None)
    assert a["comparison_fingerprint"] != c["comparison_fingerprint"]


class TimedStream(httpx.AsyncByteStream):
    def __init__(self, chunks, delay, disconnected=False):
        self.chunks = chunks
        self.delay = delay
        self.disconnected = disconnected
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            await asyncio.sleep(self.delay)
            yield chunk
        if self.disconnected:
            raise httpx.ReadError("private server details")

    async def aclose(self):
        self.closed = True


def mock_client(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


def event(delta, finish=None):
    return (
        "data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]}) + "\n\n"
    ).encode()


@pytest.mark.asyncio
async def test_content_timing_ignores_role_and_empty_chunks(tmp_path, monkeypatch):
    stream = TimedStream(
        [
            event({"role": "assistant"}),
            event({}),
            event({"content": "a"}),
            event({"content": "b"}, "stop"),
            b"data: [DONE]\n\n",
        ],
        0.015,
    )
    mock_client(monkeypatch, lambda request: httpx.Response(200, stream=stream))
    result = await run_benchmark(cfg("http://mock/v1", requests=1, warmup=0), tmp_path / "run")
    record = result.conditions[0].requests[0]
    assert record.success
    assert record.first_content_seconds >= 0.045
    assert len(record.chunk_intervals_seconds) == 1
    assert record.chunk_intervals_seconds[0] >= 0.015
    assert record.latency_seconds > record.first_content_seconds + record.chunk_intervals_seconds[0]
    assert stream.closed


@pytest.mark.asyncio
async def test_total_timeout_despite_continuous_body(tmp_path, monkeypatch):
    stream = TimedStream([event({"content": "x"})] * 100, 0.01)
    mock_client(monkeypatch, lambda request: httpx.Response(200, stream=stream))
    result = await run_benchmark(
        cfg("http://mock/v1", requests=1, warmup=0, timeout=0.04), tmp_path / "run"
    )
    record = result.conditions[0].requests[0]
    assert record.error == "timeout" and not record.success
    assert 0.03 <= record.latency_seconds < 0.2
    assert stream.closed


@pytest.mark.asyncio
async def test_cancel_retains_completed_requests(tmp_path, monkeypatch):
    calls = 0
    in_flight = asyncio.Event()
    stalled = TimedStream([sse()], 10)

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, stream=Fragmented(sse()))
        in_flight.set()
        return httpx.Response(200, stream=stalled)

    mock_client(monkeypatch, handler)
    task = asyncio.create_task(
        run_benchmark(cfg("http://mock/v1", requests=5, warmup=0), tmp_path / "run")
    )
    await asyncio.wait_for(in_flight.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = json.loads((tmp_path / "run" / "summary.json").read_text())
    records = (tmp_path / "run" / "records.jsonl").read_text().splitlines()
    assert len(records) == 1 and result["conditions"][0]["summary"]["succeeded"] == 1
    assert result["status"] == "cancelled" and stalled.closed
    assert calls == 2


@pytest.mark.asyncio
async def test_concurrency_conditions_are_sequential(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["messages"][0]["content"])
        return httpx.Response(200, stream=Fragmented(sse()))

    mock_client(monkeypatch, handler)
    result = await run_benchmark(
        cfg("http://mock/v1", requests=3, warmup=1, concurrency=[1, 2]), tmp_path / "run"
    )
    assert [c.concurrency for c in result.conditions] == [1, 2]
    assert [c.summary["total"] for c in result.conditions] == [3, 3]
    records = [
        json.loads(line) for line in (tmp_path / "run" / "records.jsonl").read_text().splitlines()
    ]
    assert [r["concurrency"] for r in records] == [1] * 4 + [2] * 4
    assert len(calls) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "streaming", "error"),
    [
        (b"data: {broken}\n\n", True, "invalid_json"),
        (b"{broken}", False, "invalid_json"),
        (b'{"choices":[]}', False, "empty_choices"),
    ],
)
async def test_invalid_payload_is_failed(tmp_path, monkeypatch, body, streaming, error):
    mock_client(monkeypatch, lambda request: httpx.Response(200, stream=Fragmented(body)))
    result = await run_benchmark(
        cfg("http://mock/v1", stream=streaming, requests=1, warmup=0), tmp_path / "run"
    )
    assert result.conditions[0].requests[0].error == error


@pytest.mark.asyncio
async def test_abrupt_transport_does_not_leak_exception(tmp_path, monkeypatch):
    stream = TimedStream([event({"content": "sensitive output"})], 0, disconnected=True)
    mock_client(monkeypatch, lambda request: httpx.Response(200, stream=stream))
    result = await run_benchmark(cfg("http://mock/v1", requests=1, warmup=0), tmp_path / "run")
    assert result.conditions[0].requests[0].error == "transport_error"
    artifacts = "".join(p.read_text() for p in (tmp_path / "run").iterdir())
    assert "private server details" not in artifacts and "sensitive output" not in artifacts


@pytest.mark.asyncio
async def test_response_close_timeout_is_not_success(tmp_path, monkeypatch):
    class SlowClose(Fragmented):
        async def aclose(self):
            await asyncio.sleep(0.1)

    mock_client(monkeypatch, lambda request: httpx.Response(200, stream=SlowClose(sse())))
    result = await run_benchmark(
        cfg("http://mock/v1", requests=1, warmup=0, timeout=0.02), tmp_path / "run"
    )
    record = result.conditions[0].requests[0]
    assert not record.success and record.error == "timeout"
    assert result.conditions[0].summary["succeeded"] == 0


def test_redaction_handles_mixed_hex_case():
    from tokenperf.storage import redact

    assert redact("Secret%2fkey%2Bvalue", "Secret/key+value") == "[REDACTED]"
    assert redact("Secret%2Fkey%2bvalue", "Secret/key+value") == "[REDACTED]"


@pytest.mark.asyncio
async def test_extreme_usage_is_unavailable(tmp_path, monkeypatch):
    payload = {
        "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
        "usage": {
            "completion_tokens": 10**400,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }
    mock_client(monkeypatch, lambda request: httpx.Response(200, json=payload))
    result = await run_benchmark(
        cfg("http://mock/v1", stream=False, requests=1, warmup=0), tmp_path / "run"
    )
    record = result.conditions[0].requests[0]
    assert (
        record.success and record.output_tokens is None and record.output_tokens_per_second is None
    )
