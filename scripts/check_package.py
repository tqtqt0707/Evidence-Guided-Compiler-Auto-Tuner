"""Build from an sdist and exercise the wheel in a clean, disposable venv.

Run with Python 3.11+ on x86-64 Linux with LLVM 21. Build requirements use
the configured pip index; no web/graph dependencies are needed by this check.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import select


def run(args, *, cwd, env=None):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(map(str, args))}\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
    return result.stdout


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="autotuner-package-") as directory:
        stage = Path(directory)
        source = stage / "source"
        shutil.copytree(root, source, ignore=shutil.ignore_patterns(
            ".git", ".env", ".env.*", ".venv", "node_modules", "build", "dist", "__pycache__", ".pytest_cache", "*.egg-info", "*.db*", ".coverage", "test-results", "htmlcov", "browser-evidence.png"))
        build_tools = stage / "build-tools"
        run([sys.executable, "-m", "pip", "install", "--quiet", "--no-deps", "--target", build_tools, "setuptools>=75"], cwd=stage)
        build_env = dict(os.environ, PYTHONPATH=str(build_tools))
        run([sys.executable, "setup.py", "sdist", "--dist-dir", stage / "sdist"], cwd=source, env=build_env)
        sdist = next((stage / "sdist").glob("*.tar.gz"))
        run([sys.executable, "-m", "pip", "wheel", sdist, "--no-deps", "--wheel-dir", stage / "wheels"], cwd=stage)
        wheel = next((stage / "wheels").glob("*.whl"))
        run([sys.executable, "-m", "venv", stage / "venv"], cwd=stage)
        python = stage / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = dict(os.environ, AUTOTUNER_DATA_DIR=str(stage / "data"), AUTOTUNER_LLM_MODE="off")
        env.pop("OPENAI_API_KEY", None)
        env.pop("AUTOTUNER_ENV_FILE", None)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env.pop("AUTOTUNER_ORCHESTRATOR", None)
        run([python, "-m", "pip", "install", "--quiet", wheel], cwd=stage, env=env)
        probe = json.loads(run([python, "-c", "from autotuner.config import ROOT; import json,hashlib; print(json.dumps({'root': str(ROOT), 'bundles':len(list((ROOT/'examples').glob('full-*/bundle.json'))),'web':(ROOT/'web/index.html').is_file(),'files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob('*') if p.is_file()}}))"], cwd=stage, env=env))
        assert str(source) not in probe["root"] and str(root) not in probe["root"], probe
        assert probe["bundles"] == 3 and probe["web"], probe
        for relative, digest in probe.pop("files").items():
            assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest, relative
        assert len(json.loads(run([python, "-m", "autotuner", "benchmarks"], cwd=stage, env=env))) == 5
        doctor = json.loads(run([python, "-m", "autotuner", "doctor"], cwd=stage, env=env))
        tune = run([python, "-m", "autotuner", "tune", "--benchmark", "dot", "--budget", "1", "--size", "7", "--seed", "11", "--repeat", "2"], cwd=stage, env=env)
        summary = json.loads(tune[tune.index("{"):])
        assert summary["status"] == "completed"
        experiment_id = summary["experiment_id"]
        exported = json.loads(run([python, "-m", "autotuner", "report", experiment_id, "--output-dir", stage / "report"], cwd=stage, env=env))
        assert summary == exported
        status = json.loads(run([python, "-m", "autotuner", "status", experiment_id], cwd=stage, env=env))
        assert status["status"] == "completed"
        trials = json.loads(run([python, "-m", "autotuner", "trials", experiment_id], cwd=stage, env=env))
        assert trials["total"] == 1
        assert json.loads(run([python, "-m", "autotuner", "actions", "--include-unavailable"], cwd=stage, env=env))["items"]
        calibrated = json.loads(run([python, "-m", "autotuner", "calibrate", "--benchmark", "dot"], cwd=stage, env=env))
        assert len(calibrated["benchmarks"]["dot"]["cases"]) == 3
        retry_output = run([python, "-m", "autotuner", "retry", experiment_id], cwd=stage, env=env)
        retried = json.loads(retry_output[retry_output.index("{"):])
        retry_status = json.loads(run([python, "-m", "autotuner", "status", retried["experiment_id"]], cwd=stage, env=env))
        assert retry_status["parent_experiment_id"] == experiment_id
        active = subprocess.Popen([str(python), "-m", "autotuner", "tune", "--benchmark", "dot", "--mode", "full", "--budget", "20", "--size", "2000000", "--repeat", "5"],
                                  cwd=stage, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert select.select([active.stdout], [], [], 45)[0], "CLI did not announce its experiment"
            first_line = active.stdout.readline()
            assert first_line.startswith("Experiment "), first_line
            active_id = first_line.split()[1]
            cancelled = json.loads(run([python, "-m", "autotuner", "cancel", active_id], cwd=stage, env=env))
            assert cancelled["cancellation_requested"]
            stdout, stderr = active.communicate(timeout=45)
            assert active.returncode == 130 and "CANCELLED" in stderr, (stdout, stderr)
            assert json.loads(run([python, "-m", "autotuner", "status", active_id], cwd=stage, env=env))["status"] == "cancelled"
        finally:
            if active.poll() is None:
                active.kill()
                active.communicate(timeout=10)
        blocked = subprocess.run([str(python), "-m", "autotuner", "serve", "--host", "0.0.0.0"], cwd=stage, env=env, capture_output=True, text=True, timeout=10)
        assert blocked.returncode == 1 and "Only 127.0.0.1" in blocked.stderr
        run([python, "-m", "autotuner.examples", experiment_id, "packaged-dot", "--output-dir", stage / "examples"], cwd=stage, env=env)
        assert json.loads((stage / "examples/packaged-dot/bundle.json").read_text())["summary"] == summary
        assert json.loads((stage / "report/summary.json").read_text()) == summary
        print(json.dumps({"status": "passed", "sdist": True, "wheel": True, "clean_venv": True,
                          "actions": len(doctor["catalog"]["items"]), "resources": probe,
                          "cli_experiment": "completed", "report_export": "passed", "portable_example": "passed"}, indent=2))


if __name__ == "__main__":
    main()
