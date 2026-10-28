from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from autotuner import calibration, cli, config, service
from autotuner.actions import compose
from autotuner.models import ExperimentRequest, Workload
from autotuner.policies import Proposal
from autotuner.registry import benchmark
from autotuner.service import TuningService, TrialFailure, ExperimentCancelled
from autotuner.storage import Store
from autotuner.toolchain import CommandResult, execute


@pytest.mark.parametrize("fault,expected_status", [
    ("unknown pass", "pass_parse_failed"), ("Broken module", "ir_verification_failed"),
    ("compiler exit", "compile_failed"), ("disassembly", "ir_verification_failed"),
    ("link", "compile_failed"), ("runtime exit", "execution_failed"),
    ("timeout", "timeout"), ("malformed", "checksum_mismatch"),
    ("wrong checksum", "checksum_mismatch"),
])
def test_candidate_failure_is_rejected_before_search(fault, expected_status, tmp_path):
    request = ExperimentRequest.parse({"benchmark_id": "dot", "mode": "quick",
                                       "tuning_input": {"size": 7, "seed": 11, "repeat": 2}})
    store = Store()
    run_id = store.create_experiment(request.as_dict(), "source", {}, {"catalog_hash": "catalog"})
    def result(*, returncode=0, stdout="", stderr="", timed_out=False):
        return CommandResult(["injected-stage"], returncode, 100, timed_out, stdout, stderr, "out", "err")
    class FaultyToolchain:
        def optimize(self, seed, actions, output, logs):
            output.write_bytes(b"bitcode")
            if fault in {"unknown pass", "Broken module", "compiler exit"}:
                return result(returncode=1, stderr=fault)
            return result()
        def textual_ir(self, *args):
            if fault == "disassembly":
                raise RuntimeError("invalid bitcode")
            return "  %1 = add i64 1, 2"
        def object_and_link(self, bitcode, output, logs, label):
            output.write_bytes(b"binary")
            return [result(returncode=1 if fault == "link" else 0, stderr="link failure")]
        def run_binary(self, *args):
            return result(returncode=1 if fault == "runtime exit" else -9 if fault == "timeout" else 0,
                          stdout="debug text" if fault == "malformed" else "checksum:0000000000000000\n",
                          timed_out=fault == "timeout")
    svc = TuningService(store)
    svc._toolchain = FaultyToolchain()
    proposal = Proposal(("dce",), "test", "failure injection")
    trial = svc._trial(run_id, config.RUNS_DIR / run_id, tmp_path / "seed.bc", "baseline IR", {"instruction_count": 1}, request,
                       benchmark("dot"), {"small_0": "checksum:1111111111111111\n"}, "guided", 1, proposal, "fingerprint")
    assert trial["status"] == expected_status
    assert trial["search_median_ns"] is None
    assert svc._select_winner([trial]) is None
    assert not store.all("SELECT * FROM samples WHERE phase='search'")
    assert not store.all("SELECT * FROM samples WHERE valid=1")
    detail = svc.get_trial(run_id, trial["id"])
    assert detail["commands"], "failed build commands must remain inspectable"


@pytest.mark.parametrize("error,exit_code", [(TrialFailure("compile_failed", "injected compiler failure"), 1),
                                          (ExperimentCancelled(), 130),
                                          (FileNotFoundError("missing tool"), 1),
                                          (KeyboardInterrupt(), 130),
                                          (subprocess.TimeoutExpired("clang", 1), 1)])
def test_cli_returns_clean_failure_without_traceback(error, exit_code, monkeypatch, capsys):
    class FakeService:
        def create_experiment(self, request): return "run"
        def run_experiment(self, run_id): raise error
    monkeypatch.setattr(cli, "TuningService", lambda reconcile=False: FakeService())
    assert cli.main(["tune", "--benchmark", "dot"]) == exit_code
    stderr = capsys.readouterr().err
    assert stderr.startswith("error:")
    assert "Traceback" not in stderr


def test_calibration_rejects_nondeterministic_checksum(monkeypatch):
    class FakeToolchain:
        count = 0
        def environment(self): return {}
        def compile_direct(self, *args): return SimpleNamespace(ok=True)
        def run_binary(self, *args):
            self.count += 1
            return SimpleNamespace(ok=True, duration_ns=100, stdout=f"checksum:{self.count:016x}\n")
    monkeypatch.setattr(calibration, "Toolchain", FakeToolchain)
    with pytest.raises(RuntimeError, match="checksum changed"):
        calibration.calibrate("dot")


def test_timeout_terminates_descendant_process_and_next_command_runs(tmp_path):
    pid_file = tmp_path / "descendant.pid"
    source = ("import subprocess,sys,time,pathlib; "
              "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
              f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); time.sleep(30)")
    outcome = execute([sys.executable, "-c", source], tmp_path, "tree", timeout=1)
    assert outcome.timed_out
    pid = int(pid_file.read_text())
    stat = Path(f"/proc/{pid}/stat")
    assert not stat.exists() or stat.read_text().rsplit(")", 1)[1].split()[0] == "Z", "descendant is still executing"
    followup = execute([sys.executable, "-c", "print('next command')"], tmp_path, "next", timeout=2)
    assert followup.ok
    assert followup.stdout == "next command\n"
    assert json.loads((tmp_path / "tree.command.json").read_text())[0]["timed_out"]


def test_keyboard_interrupt_marks_experiment_and_releases_worker(monkeypatch):
    svc = TuningService()
    request = ExperimentRequest.parse({"benchmark_id": "dot", "mode": "quick"})
    run_id = svc.store.create_experiment(request.as_dict(), "hash", {}, {"catalog_hash": "test"})
    def interrupt(): raise KeyboardInterrupt()
    monkeypatch.setattr(svc, "preflight", interrupt)
    with pytest.raises(KeyboardInterrupt):
        svc.run_experiment(run_id)
    row = svc.get_experiment(run_id)
    assert row["status"] == "interrupted"
    assert row["error_code"] == "USER_INTERRUPTED"
    assert svc.store.one("SELECT experiment_id FROM worker_slot")["experiment_id"] is None


def test_keyboard_interrupt_kills_spawned_process(tmp_path, monkeypatch):
    from autotuner import toolchain
    original = subprocess.Popen
    processes = []
    class InterruptedProcess(original):
        interrupted = False
        def wait(self, *args, **kwargs):
            if not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt()
            return super().wait(*args, **kwargs)
    def launch(*args, **kwargs):
        process = InterruptedProcess(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(toolchain.subprocess, "Popen", launch)
    with pytest.raises(KeyboardInterrupt):
        execute([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, "interrupt", timeout=60)
    try:
        assert processes[0].poll() is not None, "interrupted compiler is still executing"
    finally:
        if processes[0].poll() is None:
            processes[0].kill()
            processes[0].wait()
