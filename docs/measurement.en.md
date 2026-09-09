# Measurement methodology

[简体中文](measurement.md) | **English**

[README](../README.en.md) · [Publishing guide](releasing.en.md)

## Timing boundaries

All durations use a monotonic clock. Request latency includes HTTPX request construction, connection pooling, DNS/TLS, network time, and client parsing overhead. It is not pure server inference time. Protocol completion means receiving `[DONE]` for streaming responses or receiving the complete body for non-streaming responses; it does not wait for the server to close a keep-alive TCP connection.

Initial connections and reused connections may behave differently. Each condition starts with warmup requests, and warmup and measurement share a client. The matrix also reuses that client. A small number of warmups does not guarantee that all concurrent connections have been warmed up.

All requests in a condition are considered enqueued at the start of its measurement phase. A fixed number of workers take the next request, so `queue_seconds` grows with waiting time. This is a closed-loop, fixed-concurrency test, not an open-loop load test with a fixed arrival rate.

The total request timeout covers response headers and the complete body. Continuous streaming activity does not extend the deadline indefinitely. There are no retries or automatic redirects; environment proxy settings are not read by default.

## Text and tokens

Only nonempty text counts as first content. Refusals, tool calls, and content filtering are not successful text results in this version. `stop` and `length` are normal completion reasons. Empty text, malformed responses, missing termination markers, and connection errors are failures. Streaming requires a valid text completion event and `[DONE]`; permissive compatibility implementations that omit the termination marker are classified as protocol errors.

A chunk may contain multiple tokens, so chunk intervals are not called ITL. The first-chunk timestamp and total token count cannot recover an exact generation-phase token rate. `generation_tokens_per_second` is reserved and remains null rather than implying unsupported precision.

`output_tokens_per_second` is end-to-end output token throughput: trustworthy text completion tokens / complete request latency, including the wait for first content. It is calculated only when usage explicitly excludes hidden reasoning and other non-text tokens. If the service omits token details, the reported completion token count is retained, but this rate is null. This is more conservative than a typical generation-phase tokens/s metric; the two are not interchangeable.

Token counts are not reconstructed from character counts or an assumed local tokenizer. A single-token response can still have usage recorded, but no per-token interval is inferred.

## Aggregation

Success rate uses completed measured samples as its denominator. Cancelled runs may contain fewer samples than requested, so interpret counts alongside run status. Time spent on failed requests remains part of the measurement window; failed-request latencies are summarized separately.

Percentiles use linear interpolation over sorted values. Empty samples produce null values. P95/P99 from small samples do not establish a stable tail-latency distribution.

Throughput uses the condition's measurement wall-clock time. Summing individual concurrent request durations would give the wrong denominator. Warmup records are stored separately from measured samples and excluded from percentiles, error rates, and throughput.

TokenPerf does not infer server hardware capability, remove public-network latency or client event-loop overhead, or guarantee that provider-reported usage is correct.
