# Publishing TokenPerf

[简体中文](releasing.md) | **English**

[README](../README.en.md) · [Measurement methodology](measurement.en.md)

## Status and prerequisites

The repository includes build, verification, and publishing workflows. Public releases require the project owner's PyPI / TestPyPI permissions. The workflow does not create accounts or configure Trusted Publishers.

On 2026-09-08, requests to `/pypi/tokenperf/json` on both indexes returned 404. No published project was found, but this does not guarantee that the name is unreserved or will remain available.

## Configure Trusted Publishers

Create a pending publisher on both TestPyPI and PyPI:

- Owner: `DrizzleWoods`
- Repository: `tokenperf`
- Workflow: `publish.yml`
- Environment: `testpypi` for TestPyPI, `pypi` for PyPI

Create the corresponding GitHub environments. A required reviewer is recommended for the production `pypi` environment. GitHub OIDC avoids storing a long-lived PyPI token in the repository. See the [official Trusted Publishing documentation](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

## Release steps

1. When changing the version, keep `pyproject.toml`, `tokenperf.__version__`, and the result's `tool_version` consistent. Run tests and build the package.
2. Commit and push the reviewed code, then manually run the `Publish` workflow from that revision.
3. The workflow runs Ruff, pytest, the build, and Twine metadata checks.
4. After uploading to TestPyPI, it creates a fresh virtual environment. Dependencies are installed from production PyPI, while the exact TokenPerf version is installed exclusively from TestPyPI to avoid selecting the wrong distribution across mixed indexes.
5. After imports and the CLI are verified, the same distribution files from the build job are uploaded to PyPI.

If TestPyPI upload succeeds but a later job fails, rerun the failed jobs in GitHub instead of uploading existing files again. Files for an existing version cannot be overwritten; publish a new version if the build must change. Do not use `skip-existing` to ignore inconsistent artifacts.

## Local verification

```bash
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
uv run twine check dist/*
```

Install the wheel in a clean virtual environment outside the repository and verify `import tokenperf`, `tokenperf --help`, and `tokenperf --version`. A successful build and local installation do not establish that TestPyPI installation verification or production publication has occurred.
