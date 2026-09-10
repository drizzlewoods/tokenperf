"""Strict text-completion parsing. Transport errors never expose response bodies."""

import json
from time import perf_counter

import httpx


class ProtocolError(Exception):
    pass


def usage_tokens(usage: object) -> tuple[int | None, bool]:
    if not isinstance(usage, dict):
        return None, False
    tokens = usage.get("completion_tokens")
    if type(tokens) is not int or not 0 <= tokens <= 2**53 - 1:
        return None, False
    details = usage.get("completion_tokens_details")
    # Hidden reasoning/prediction tokens invalidate content-chunk token timing.
    safe = (
        isinstance(details, dict)
        and type(details.get("reasoning_tokens")) is int
        and details["reasoning_tokens"] == 0
    )
    if safe:
        safe = all(
            type(value) is int and value == 0
            for key, value in details.items()
            if key != "reasoning_tokens"
        )
    return tokens, safe


class Completion:
    def __init__(self) -> None:
        self.times: list[float] = []
        self.tokens: int | None = None
        self.rate_safe = False
        self.finished = False
        self.has_content = False

    def consume(self, data: object, stream: bool) -> None:
        if not isinstance(data, dict) or "error" in data:
            raise ProtocolError("protocol_error")
        if data.get("usage") is not None:
            self.tokens, self.rate_safe = usage_tokens(data["usage"])
        choices = data.get("choices")
        if not isinstance(choices, list):
            raise ProtocolError("protocol_error")
        if not choices:
            if data.get("usage") is None:
                raise ProtocolError("empty_choices")
            return
        if len(choices) != 1 or not isinstance(choices[0], dict):
            raise ProtocolError("unsupported_choices")
        choice = choices[0]
        if choice.get("index", 0) != 0:
            raise ProtocolError("unsupported_choices")
        message = choice.get("delta" if stream else "message")
        if not isinstance(message, dict):
            raise ProtocolError("protocol_error")
        if message.get("tool_calls") or message.get("function_call"):
            raise ProtocolError("non_text_completion")
        if message.get("refusal"):
            raise ProtocolError("refusal")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ProtocolError("non_text_completion")
        if content:
            if self.finished:
                raise ProtocolError("content_after_finish")
            self.has_content = True
            if stream:
                self.times.append(perf_counter())
        finish = choice.get("finish_reason")
        if finish is not None:
            if finish not in {"stop", "length"}:
                raise ProtocolError("non_text_finish")
            self.finished = True

    def validate(self) -> None:
        if not self.finished:
            raise ProtocolError("missing_finish_reason")
        if not self.has_content:
            raise ProtocolError("empty_completion")


async def parse_stream(response: httpx.Response) -> Completion:
    result = Completion()
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if not data_lines:
                continue
            raw = "\n".join(data_lines)
            data_lines.clear()
            if raw.strip() == "[DONE]":
                result.validate()
                return result
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                raise ProtocolError("invalid_json") from None
            result.consume(data, stream=True)
        elif line.startswith("data:"):
            data_lines.append(line[5:].removeprefix(" "))
        # Comments and unknown SSE fields are ignored by the SSE specification.
    raise ProtocolError("unexpected_eof")


async def parse_nonstream(response: httpx.Response) -> Completion:
    await response.aread()
    try:
        data = response.json()
    except (ValueError, UnicodeError):
        raise ProtocolError("invalid_json") from None
    result = Completion()
    result.consume(data, stream=False)
    result.validate()
    return result
