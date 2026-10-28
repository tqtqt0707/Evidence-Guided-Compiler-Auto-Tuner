"""Every test uses disposable records and build logs, never the user's runs."""
from pathlib import Path

import pytest

from autotuner import api, calibration, config, inspection, report, service, storage


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    # Never make paid requests using a developer's real .env while testing.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AUTOTUNER_LLM_MODE", "off")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "low")
    monkeypatch.setenv("OPENAI_MAX_OUTPUT_TOKENS", "4096")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "30")
    directory = tmp_path / "data"
    runs = directory / "runs"
    monkeypatch.setenv("AUTOTUNER_DATA_DIR", str(directory))
    for module in (config, calibration, inspection, service):
        monkeypatch.setattr(module, "DATA_DIR", directory)
    for module in (config, api, report, service):
        monkeypatch.setattr(module, "RUNS_DIR", runs)
    for module in (config, storage):
        monkeypatch.setattr(module, "DB_PATH", directory / "experiments.db")
    return directory


@pytest.fixture(scope="session")
def llvm(tmp_path_factory):
    from autotuner.toolchain import Toolchain
    from autotuner.registry import benchmark
    tc = Toolchain()
    catalog = tc.validate_actions(Path(benchmark("dot")["source_path"]), tmp_path_factory.mktemp("llvm-preflight"))
    return tc, catalog
