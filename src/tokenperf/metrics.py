"""Metrics computed only from measured requests, never warmups."""

from collections import Counter

from .models import RequestRecord


def percentiles(values: list[float]) -> dict[str, float | None]:
    values = sorted(values)

    def percentile(q: float) -> float | None:
        if not values:
            return None
        position = (len(values) - 1) * q
        low = int(position)
        high = min(low + 1, len(values) - 1)
        return values[low] + (values[high] - values[low]) * (position - low)

    return {f"p{p}": percentile(p / 100) for p in (50, 95, 99)}


def summarize(records: list[RequestRecord], elapsed_seconds: float) -> dict:
    measured = [r for r in records if not r.warmup]
    good = [r for r in measured if r.success]
    bad = [r for r in measured if not r.success]
    return {
        "total": len(measured),
        "succeeded": len(good),
        "failed": len(bad),
        "success_rate": len(good) / len(measured) if measured else None,
        "errors": dict(Counter(r.error or "unknown" for r in bad)),
        "elapsed_seconds": elapsed_seconds,
        "throughput_rps": len(good) / elapsed_seconds if elapsed_seconds > 0 else None,
        "latency_seconds": percentiles([r.latency_seconds for r in good]),
        "failed_latency_seconds": percentiles([r.latency_seconds for r in bad]),
        "first_content_seconds": percentiles(
            [r.first_content_seconds for r in good if r.first_content_seconds is not None]
        ),
        "output_tokens_per_second": percentiles(
            [r.output_tokens_per_second for r in good if r.output_tokens_per_second is not None]
        ),
        "generation_tokens_per_second": percentiles(
            [
                r.generation_tokens_per_second
                for r in good
                if r.generation_tokens_per_second is not None
            ]
        ),
    }
