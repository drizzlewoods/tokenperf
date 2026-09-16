# TokenPerf

[简体中文](README.md) | **English**

A Python performance testing tool for OpenAI-compatible text APIs. Measure client-observed time to first content, end-to-end latency, success rate, and throughput under modest concurrency, with local artifacts for comparing runs.

Version `0.0.0b0` provides a Python SDK and CLI. Web, desktop, npm, and Chrome clients are planned but not implemented. Check PyPI for publication status; you can install directly from this repository.

## Installation

Requires Python 3.11 or later.

```bash
# Install from the local repository
pip install .

# Development environment
uv sync --locked
uv run tokenperf --help
```

After publication, use `pip install tokenperf` or `uv tool install tokenperf`.

## Quick start

Copy `examples/bench.json` and set `endpoint` and `model`. The endpoint accepts an API base path, such as `http://localhost:8000/v1`, or the full `/chat/completions` path.

For local services without authentication, set `"api_key_env": null`. For remote services, set an environment variable name, such as `"api_key_env": "MODEL_API_KEY"`, and inject that variable into your runtime environment. Do not put the key in the JSON configuration.

```bash
uv run tokenperf run --config examples/bench.json
# --output must be a new directory; existing results are never overwritten
uv run tokenperf run --config examples/bench.json --output tokenperf-results/model-a
uv run tokenperf compare tokenperf-results/model-a tokenperf-results/model-b
```

To use a local `.env` file, load it explicitly through uv. TokenPerf does not automatically search for credential files:

```bash
uv run --env-file .env tokenperf run --config examples/ark-smoke.json
```

Defaults: 10 measured requests, 1 warmup request, concurrency 1, streaming enabled, a 120-second total timeout per request, and no retries. The example uses `[1, 2, 4]` to demonstrate a concurrency matrix; each condition runs its warmup and measurement phases in sequence. Ten requests are suitable for a quick check, not for drawing conclusions about tail latency.

## Python SDK

```python
import asyncio
from tokenperf import BenchmarkConfig, BenchmarkResult, run_benchmark


async def main() -> BenchmarkResult:
    config = BenchmarkConfig(
        endpoint="http://localhost:8000/v1",
        model="your-model",
        api_key_env=None,
        messages=[{"role": "user", "content": "Explain a hash table briefly."}],
        generation={"max_tokens": 128, "temperature": 0},
        concurrency=[1, 2, 4],
    )
    return await run_benchmark(config)


result = asyncio.run(main())
print(result.output_dir)
```

Inside an existing event loop, such as a notebook, call `await run_benchmark(config)` directly.

## Workloads and comparisons

- `messages` defines one text conversation. `inputs` defines a list of conversations and takes precedence when set; inputs cycle in their original order. See `examples/inputs.json`.
- `seed` defaults to 0 and is sent to the service. An identical seed does not guarantee deterministic provider behavior.
- `generation` supplies generation parameters but cannot override `model/messages/stream/stream_options`. Only one text completion candidate is supported.
- Streaming requests send `stream_options.include_usage=true`. Services that reject this parameter or seed produce an HTTP status error category; no implicit fallback or retry is performed.
- The comparison fingerprint includes inputs, generation parameters, request count, warmup count, seed, streaming mode, and timeout. It excludes endpoint, model, and concurrency. Each concurrency condition is displayed separately.
- Matching configuration groups do not guarantee a fair comparison: network conditions, service load, caching, and output length still matter. TokenPerf does not automatically declare a model the fastest.

## Metrics and artifacts

See [Measurement methodology](docs/measurement.en.md) for full definitions.

| Metric | Meaning |
|---|---|
| `first_content_seconds` | Time from request start to the first nonempty text content; ignores heartbeats, role updates, and empty deltas |
| `latency_seconds` | Time from request start to protocol completion; first-content timing is not inferred for non-streaming responses |
| `queue_seconds` | Time from phase enqueue to a worker starting the request, separate from request latency |
| `chunk_intervals_seconds` | Intervals between received text chunks; **not per-token ITL** |
| `output_tokens` | Completion tokens reported by the service; null when usable usage data is unavailable |
| `throughput_rps` | Successful measured requests / condition measurement wall-clock time, including time spent on failures |

Successful and failed request latencies are summarized separately. Warmups are excluded from measured summaries. Unavailable values are `null` in JSON and `—` in the terminal.

Each run saves:

```text
tokenperf-results/<run-id>/
  config.json       # Sanitized metadata and input/generation fingerprints
  records.jsonl     # Written on completion, with condition and warmup markers
  summary.json      # Full result and condition summaries, with schema_version
```

Prompts, response text, and API keys are not saved by default. Keep your original configuration to repeat a run. Hashes in the summary are not encryption and cannot conceal the presence of guessable inputs. Endpoint and model are included as metadata. Errors contain categories rather than service error bodies that might echo inputs or keys.

Ctrl+C stops workers, preserves completed records, and writes the cancelled status. Force-killing the process may prevent a final summary from being written, but existing JSONL records remain readable.

CLI exit codes: `0` for completion without measured failures; `1` for measured failures or an incomplete run; `2` for configuration/file errors; `130` for user cancellation.

## Development and publishing

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
uv run twine check dist/*
```

The CI matrix covers Linux, Windows, macOS, and Python 3.11–3.14. A configured matrix does not mean every platform has already been verified. Publishing follows TestPyPI → installation verification → PyPI. See [Publishing guide](docs/releasing.en.md).

Roadmap: Python SDK/CLI → local Web UI → desktop installers → TypeScript SDK → Chrome entry point. Execution architecture for additional clients will be decided in later phases. Version 1 requires no background service or database.
