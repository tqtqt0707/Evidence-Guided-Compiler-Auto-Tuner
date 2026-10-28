from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Use the checkout's file even when invoked from another working directory.
# An explicit file also supports installed wheels and deployment environments.
ENV_FILE = Path(os.environ.get("AUTOTUNER_ENV_FILE", Path(__file__).resolve().parent.parent / ".env"))
load_dotenv(ENV_FILE, override=False)

ROOT = Path(__file__).resolve().parent.parent
if not (ROOT / "benchmarks" / "manifest.json").is_file():
    ROOT = Path(__file__).resolve().parent / "resources"
BENCHMARKS = ROOT / "benchmarks"
WEB = ROOT / "web"
DATA_DIR = Path(os.environ.get("AUTOTUNER_DATA_DIR", Path.home() / ".local" / "share" / "compiler-autotuner"))
DB_PATH = DATA_DIR / "experiments.db"
RUNS_DIR = DATA_DIR / "runs"

TOOL_NAMES = {"clang": "clang-21", "opt": "opt-21", "llc": "llc-21", "llvm_dis": "llvm-dis-21"}
COMPILE_TIMEOUT_S = 30
EXEC_TIMEOUT_S = 10
MAX_CAPTURE_BYTES = 65536


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
