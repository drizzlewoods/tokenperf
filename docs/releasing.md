# 发布 TokenPerf

**简体中文** | [English](releasing.en.md)

[项目说明](../README.md) · [测量说明](measurement.md)

## 当前状态与前提

仓库包含构建、验证和发布工作流。公开发布需要项目所有者的 PyPI / TestPyPI 权限，工作流本身不会创建账号或配置 Trusted Publisher。

2026-09-08 查询两个索引的 `/pypi/tokenperf/json` 均返回 404：未发现已发布项目，但这不保证名称未被保留或未来仍可注册。

## 配置 Trusted Publisher

分别在 TestPyPI 与 PyPI 创建 pending publisher：

- Owner：`DrizzleWoods`
- Repository：`tokenperf`
- Workflow：`publish.yml`
- Environment：TestPyPI 使用 `testpypi`，PyPI 使用 `pypi`

在 GitHub 建立对应 environments；建议为正式 `pypi` 环境设置发布审核者。使用 GitHub OIDC，不需要把长期 PyPI Token 写入仓库。官方说明：https://docs.pypi.org/trusted-publishers/using-a-publisher/

## 发布步骤

1. 修改版本时保持 `pyproject.toml`、`tokenperf.__version__` 和结果的 `tool_version` 一致；运行测试和构建。
2. 提交并推送经过审查的代码，再从该版本手动运行 `Publish` workflow。
3. 工作流执行 Ruff、pytest、构建和 Twine 元数据检查。
4. 上传 TestPyPI，然后建立新的虚拟环境：依赖从正式 PyPI 安装，指定版本的 TokenPerf 仅从 TestPyPI 安装，避免混用索引选中错误分发。
5. 检查导入和 CLI 成功后，将构建阶段的同一份分发文件上传 PyPI。

TestPyPI 已上传但后续失败时，在 GitHub 重跑失败任务，避免重新上传已有文件。同版本文件不能覆盖；需要修改构建时发布新版本。不要将 `skip-existing` 作为忽略不一致文件的手段。

## 本地验证

```bash
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
uv run twine check dist/*
```

在仓库之外的干净虚拟环境安装 wheel 并验证 `import tokenperf`、`tokenperf --help` 与 `tokenperf --version`。构建成功与本地安装成功不代表已经完成 TestPyPI 安装验证或正式发布。
