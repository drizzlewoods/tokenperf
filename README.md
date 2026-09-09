# TokenPerf

**简体中文** | [English](README.en.md)

面向 OpenAI 兼容文本 API 的 Python 性能测试工具。测量客户端观测到的首内容延迟、完整响应延迟、成功率和小规模并发吞吐，并保留可比较的本地结果。

当前版本为 `0.0.0b0`，提供 Python SDK 和 CLI。Web、桌面、npm 和 Chrome 插件属于后续路线，尚未实现。发布状态以 PyPI 为准；本仓库可直接安装。

## 安装

需要 Python 3.11 或更新版本。

```bash
# 从本地仓库安装
pip install .

# 开发环境
uv sync --locked
uv run tokenperf --help
```

正式发布后可使用 `pip install tokenperf` 或 `uv tool install tokenperf`。

## 快速开始

复制 `examples/bench.json`，修改 `endpoint` 和 `model`。endpoint 接受 API 基础路径（如 `http://localhost:8000/v1`）或完整 `/chat/completions` 路径。

本地无鉴权服务设置 `"api_key_env": null`。远程服务设置环境变量名，例如 `"api_key_env": "MODEL_API_KEY"`，在你的运行环境中注入该变量；不要把密钥写入配置。

```bash
uv run tokenperf run --config examples/bench.json
# --output 必须是尚不存在的目录，避免覆盖历史记录
uv run tokenperf run --config examples/bench.json --output tokenperf-results/model-a
uv run tokenperf compare tokenperf-results/model-a tokenperf-results/model-b
```

如使用本地 `.env` 文件，可显式通过 uv 加载（TokenPerf 不会自动搜索密钥文件）：

```bash
uv run --env-file .env tokenperf run --config examples/ark-smoke.json
```

默认正式请求 10 次、暖机 1 次、并发 1、流式、单请求总超时 120 秒、不重试。示例配置用 `[1, 2, 4]` 演示并发矩阵，每个条件依次暖机和测量。10 次仅适合快速测速，不适合据此解释尾延迟。

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

在已有异步事件循环（例如 Notebook）中直接 `await run_benchmark(config)`。

## 工作负载与对比

- `messages` 定义一个文本会话；`inputs` 定义会话列表，设置后优先使用，按原始顺序循环。见 `examples/inputs.json`。
- `seed` 默认 0，会传给服务。相同 seed 不保证供应商侧完全确定性。
- `generation` 传递生成参数，但不能覆盖 `model/messages/stream/stream_options`；仅支持单个文本候选。
- 流式请求发送 `stream_options.include_usage=true`。不支持该参数或 seed 的服务会显示原始 HTTP 状态类别，不做隐式降级或重试。
- 对比指纹包含输入、生成参数、请求数、暖机、seed、流式模式、超时；不包含 endpoint、model、并发。不同并发单独展示。
- 同一配置组不等于公平性保证：网络、服务负载、缓存、输出长度仍影响结果。工具不自动评选“最快模型”。

## 指标与结果

完整定义见 [测量说明](docs/measurement.md)。

| 指标 | 含义 |
|---|---|
| `first_content_seconds` | 从发起请求到首个非空文本内容；忽略心跳、role 和空 delta |
| `latency_seconds` | 从发起请求到协议响应完成；非流式不推测首内容时间 |
| `queue_seconds` | 本轮排队到工作者开始请求的时间，独立于请求延迟 |
| `chunk_intervals_seconds` | 文本内容块的接收间隔，**不是逐 token ITL** |
| `output_tokens` | 服务报告的 completion tokens；无可靠 usage 时为空 |
| `throughput_rps` | 成功正式请求数 / 条件测量墙钟时间，包含失败消耗的时间 |

成功与失败延迟分开统计；暖机不计入正式汇总。无可测数据时 JSON 使用 `null`，终端显示 `—`。

每次运行保存：

```text
tokenperf-results/<run-id>/
  config.json       # 脱敏元信息与输入/生成参数指纹
  records.jsonl     # 每次完成即写入，包含条件与暖机标记
  summary.json      # 带 schema_version 的完整结果与各条件汇总
```

默认不保存提示词、回答正文或密钥；请自行保留原始配置以重新运行。摘要中的哈希不是加密，也不能隐藏可猜测输入的存在。endpoint 和 model 属于结果元数据。错误仅记录类别，不记录可能回显输入或密钥的服务错误正文。

Ctrl+C 会停止工作者，保留已完成记录并写入取消状态。强制杀进程可能来不及生成最终摘要，但已写出的 JSONL 仍可读取。

CLI 退出码：`0` 完成且无正式失败；`1` 有正式失败或运行未完成；`2` 配置/文件错误；`130` 用户取消。

## 开发与发布

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
uv run twine check dist/*
```

CI 配置覆盖 Linux、Windows、macOS 与 Python 3.11–3.14；配置了矩阵不代表所有平台已经实际验证。发布采用 TestPyPI → 安装验证 → PyPI 的顺序，详见 [发布说明](docs/releasing.md)。

路线：Python SDK/CLI → 本地 Web → 桌面安装包 → TypeScript SDK → Chrome 入口。各端执行方式在后续阶段确定，首版不依赖后台服务或数据库。
