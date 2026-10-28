"""Independent small-input reference calculations for the C benchmarks."""

from __future__ import annotations

import re

from .models import Workload

MASK = 2**64 - 1
FNV_OFFSET = 14695981039346656037
FNV_PRIME = 1099511628211
OUTPUT_RE = re.compile(r"\Achecksum:([0-9a-f]{16})\n\Z")


def next_u64(state: int) -> tuple[int, int]:
    state = (state + 0x9E3779B97F4A7C15) & MASK
    value = state
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK
    return state, value ^ (value >> 31)


def hash_u64(current: int, value: int) -> int:
    for shift in range(0, 64, 8):
        current = ((current ^ ((value >> shift) & 255)) * FNV_PRIME) & MASK
    return current


def expected_small(benchmark_id: str, workload: Workload) -> str:
    n, state = workload.size, workload.seed
    if benchmark_id == "dot":
        a, b = [], []
        for _ in range(n):
            state, value = next_u64(state)
            a.append(value & 1023)
            state, value = next_u64(state)
            b.append(value & 1023)
        result = FNV_OFFSET
        for r in range(workload.repeat):
            a[r % n] ^= r + 1
            total = sum(x * y for x, y in zip(a, b)) & MASK
            result = hash_u64(result, total)
    elif benchmark_id == "matmul":
        a, b = [], []
        for _ in range(n * n):
            state, value = next_u64(state)
            a.append(value & 255)
            state, value = next_u64(state)
            b.append(value & 255)
        result = FNV_OFFSET
        for r in range(workload.repeat):
            a[r % (n * n)] ^= r + 1
            for i in range(n):
                for j in range(n):
                    cell = sum(a[i * n + k] * b[k * n + j] for k in range(n)) & MASK
                    result = hash_u64(result, cell)
    elif benchmark_id == "pipeline":
        a, b = [], []
        for _ in range(n):
            state, value = next_u64(state)
            a.append(value & 1023)
            state, value = next_u64(state)
            b.append(value & 1023)
        result = FNV_OFFSET
        for r in range(workload.repeat):
            a[r % n] ^= r + 1
            stage = [3 * x + y for x, y in zip(a, b)]
            total = sum(x * x + (x >> 7) for x in stage) & MASK
            result = hash_u64(result, total)
    elif benchmark_id in ("histogram", "skew_histogram"):
        values = []
        for _ in range(n):
            state, value = next_u64(state)
            raw = value & 0xFFFFFFFF
            values.append(((raw | 1) if raw & 7 else (raw & ~1))
                          if benchmark_id == "skew_histogram" else raw)
        result = FNV_OFFSET
        for r in range(workload.repeat):
            values[r % n] ^= r + 1
            bins = [0] * 256
            for value in values:
                if value & 1:
                    key = (value >> 8) & 255
                elif value & 2:
                    key = (value >> 16) & 255
                else:
                    key = value & 255
                bins[key] += 1
            assert sum(bins) == n
            for count in bins:
                result = hash_u64(result, count)
    else:
        raise ValueError(f"Unknown benchmark: {benchmark_id}")
    return f"checksum:{result:016x}\n"


def parse_checksum(stdout: str) -> str:
    if not OUTPUT_RE.fullmatch(stdout):
        raise ValueError("Expected exactly one checksum:<16 lowercase hex digits> line")
    return stdout
