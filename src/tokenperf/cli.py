"""Terminal interface; the engine remains independent of Typer and Rich."""

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from tokenperf import __version__
from tokenperf.engine import run_benchmark
from tokenperf.models import BenchmarkConfig, BenchmarkResult

app = typer.Typer(help="可重复的 LLM API 客户端性能测试。", pretty_exceptions_enable=False)
console = Console(markup=False)


def _version(value: bool) -> None:
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="显示版本。")
    ] = False,
) -> None:
    """TokenPerf：测量首内容延迟、完整响应延迟和稳定性。"""


def _number(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.3f}"
    return "—"


def _table(results: list[BenchmarkResult], *, compare: bool = False) -> Table:
    table = Table(title="客户端观测结果（秒）；— 表示不可测或无样本")
    for label in (
        "模型",
        "运行 / 状态",
        "并发",
        "成功 / 总数",
        "首内容 P50",
        "总延迟 P95",
        "成功 req/s",
    ):
        table.add_column(label)
    if compare:
        table.add_column("配置组")
    groups: dict[str, str] = {}
    for result in results:
        fingerprint = result.config.get("comparison_fingerprint")
        # Missing fingerprint is never evidence of comparable configurations.
        key = fingerprint if isinstance(fingerprint, str) and fingerprint else result.run_id
        group = groups.setdefault(key, str(len(groups) + 1))
        for condition in result.conditions:
            summary = condition.summary
            row = [
                str(result.config.get("model", "unknown")),
                f"{result.run_id[:8]} / {result.status}",
                str(condition.concurrency),
                f"{summary['succeeded']} / {summary['total']}",
                _number(summary.get("first_content_seconds", {}).get("p50")),
                _number(summary.get("latency_seconds", {}).get("p95")),
                _number(summary.get("throughput_rps")),
            ]
            if compare:
                row.append(group)
            table.add_row(*row)
    return table


@app.command()
def run(
    config: Annotated[Path, typer.Option("--config", help="JSON 配置文件。")],
    output: Annotated[
        Path | None, typer.Option("--output", help="新的结果目录，不覆盖已有目录。")
    ] = None,
) -> None:
    """运行测试；每个并发条件依次执行。失败样本返回退出码 1。"""
    try:
        settings = BenchmarkConfig.model_validate_json(config.read_text(encoding="utf-8"))
    except ValidationError as exc:
        # Pydantic's normal exception representation can include prompts and secrets.
        fields = sorted({".".join(map(str, item["loc"])) or "config" for item in exc.errors()})
        console.print("配置无效，请检查字段：" + ", ".join(fields))
        raise typer.Exit(2) from None
    except (OSError, UnicodeError):
        console.print("无法读取 UTF-8 JSON 配置文件。")
        raise typer.Exit(2) from None
    console.print("开始测试；默认不重试。小样本结果仅适合快速测速。")
    try:
        with console.status("正在请求模型 API…", spinner="dots"):
            result = asyncio.run(run_benchmark(settings, output_dir=output))
    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print("测试已中断；已完成的记录保留在结果目录中。")
        raise typer.Exit(130) from None
    except (ValueError, OSError):
        console.print("无法启动或保存测试：请检查密钥环境变量、输出目录及写入权限。")
        raise typer.Exit(2) from None
    console.print(_table([result]))
    console.print(f"结果目录：{result.output_dir}")
    if result.status != "completed" or any(c.summary["failed"] for c in result.conditions):
        raise typer.Exit(1)


@app.command()
def compare(
    directories: Annotated[list[Path], typer.Argument(help="两个或更多运行结果目录。")],
) -> None:
    """比较历史结果；配置组不同的结果不作等条件比较。"""
    if len(directories) < 2:
        console.print("请提供至少两个结果目录。")
        raise typer.Exit(2)
    results = []
    try:
        for directory in directories:
            raw = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
            if raw.get("schema_version") != "1":
                raise ValueError("unsupported schema")
            results.append(BenchmarkResult.model_validate(raw))
        fingerprints = [result.config.get("comparison_fingerprint") for result in results]
        if any(item is not None and not isinstance(item, str) for item in fingerprints):
            raise ValueError("invalid fingerprint")
        table = _table(results, compare=True)
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        console.print("结果文件缺失、损坏或格式版本不受支持。")
        raise typer.Exit(2) from None
    console.print(table)
    console.print("同配置组仅表示输入与测试设置一致；网络、实际输出长度和服务负载仍可能不同。")
    fingerprints = [result.config.get("comparison_fingerprint") for result in results]
    if any(not item for item in fingerprints) or len(set(fingerprints)) > 1:
        console.print("配置不一致或缺少配置指纹：不能直接据此判定模型快慢。")
    if any(result.status != "completed" for result in results):
        console.print("包含未完成的运行，请结合状态和已完成样本数解读。")
