from __future__ import annotations

import shutil
import subprocess

import pytest

from autotuner.models import Workload
from autotuner.oracle import expected_small, parse_checksum
from autotuner.registry import registry


@pytest.mark.skipif(not shutil.which("clang-21"), reason="LLVM 21 is required")
def test_independent_oracle_matches_all_c_benchmarks(tmp_path):
    for entry in registry().values():
        binary = tmp_path / entry["id"]
        subprocess.run(["clang-21", "-std=c17", "-O0", entry["source_path"], "-o", str(binary)], check=True)
        for item in entry["small_cases"]:
            workload = Workload.parse(item, entry)
            actual = subprocess.run([str(binary), *workload.args()], capture_output=True, text=True, check=True).stdout
            assert actual == expected_small(entry["id"], workload)


def test_malformed_or_duplicate_checksum_is_rejected():
    for output in ("", "checksum:ABCDEF0000000000\n", "checksum:0000000000000000\nchecksum:0000000000000000\n", "debug\nchecksum:0000000000000000\n"):
        with pytest.raises(ValueError):
            parse_checksum(output)
