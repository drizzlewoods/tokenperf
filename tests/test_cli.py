import json

import pytest
from typer.testing import CliRunner

from tokenperf.cli import app

runner = CliRunner()


def test_help_and_version():
    assert runner.invoke(app, ["--help"]).exit_code == 0
    assert runner.invoke(app, ["--version"]).stdout.strip() == "0.0.0b0"


def test_config_errors_do_not_echo_secrets(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"endpoint": "secret-key", "unknown": "private-prompt"}))
    result = runner.invoke(app, ["run", "--config", str(path)])
    assert result.exit_code == 2
    assert "secret-key" not in result.stdout
    assert "private-prompt" not in result.stdout


@pytest.mark.parametrize("content", ['{"schema_version":"future"}', "{}", "[]", "invalid"])
def test_compare_rejects_invalid_reports(tmp_path, content):
    (tmp_path / "summary.json").write_text(content)
    result = runner.invoke(app, ["compare", str(tmp_path), str(tmp_path)])
    assert result.exit_code == 2
    assert "格式版本" in result.stdout


def test_compare_requires_two_directories(tmp_path):
    result = runner.invoke(app, ["compare", str(tmp_path)])
    assert result.exit_code == 2


def test_run_and_compare_with_mock_endpoint(tmp_path, monkeypatch):
    import httpx

    import tokenperf.engine as engine

    real_client = httpx.AsyncClient

    def handler(request):
        payload = json.loads(request.content)
        assert payload["seed"] == 0
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
                "usage": {"completion_tokens": 1},
            },
        )

    monkeypatch.setattr(
        engine.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    settings = {
        "endpoint": "https://example.com/v1",
        "model": "test-model",
        "api_key_env": None,
        "stream": False,
        "requests": 2,
        "warmup": 1,
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(settings))
    first, second = tmp_path / "first", tmp_path / "second"
    for output in (first, second):
        result = runner.invoke(app, ["run", "--config", str(path), "--output", str(output)])
        assert result.exit_code == 0, result.exception
        assert "结果目录" in result.stdout
        records = [json.loads(line) for line in (output / "records.jsonl").read_text().splitlines()]
        assert len(records) == 3
    result = runner.invoke(app, ["compare", str(first), str(second)])
    assert result.exit_code == 0, result.exception
    assert "配置不一致" not in result.stdout
    raw = json.loads((second / "summary.json").read_text())
    raw["config"]["comparison_fingerprint"] = "changed"
    (second / "summary.json").write_text(json.dumps(raw))
    result = runner.invoke(app, ["compare", str(first), str(second)])
    assert result.exit_code == 0
    assert "配置不一致" in result.stdout
    raw["conditions"][0]["summary"] = {}
    (second / "summary.json").write_text(json.dumps(raw))
    result = runner.invoke(app, ["compare", str(first), str(second)])
    assert result.exit_code == 2
    assert "格式版本" in result.stdout
    result = runner.invoke(app, ["run", "--config", str(path), "--output", str(first)])
    assert result.exit_code == 2


def test_missing_key_is_safe(tmp_path, monkeypatch):
    monkeypatch.delenv("TOKENPERF_MISSING_TEST_KEY", raising=False)
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "endpoint": "https://example.com/v1",
                "model": "test",
                "api_key_env": "TOKENPERF_MISSING_TEST_KEY",
            }
        )
    )
    result = runner.invoke(app, ["run", "--config", str(path)])
    assert result.exit_code == 2
    assert "Traceback" not in result.stdout
