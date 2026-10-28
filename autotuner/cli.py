from __future__ import annotations

import argparse
import json
import sys
import subprocess
from pathlib import Path

from .service import TuningService, TrialFailure, ExperimentCancelled


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="autotuner", description="Correctness-checked LLVM pass tuning")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Probe LLVM tools and action templates")
    commands.add_parser("llm-status", help="Show OpenAI configuration without making a paid request")
    commands.add_parser("benchmarks", help="List built-in benchmarks")
    calibration = commands.add_parser("calibrate", help="Measure provisional quick/tuning/holdout workloads")
    calibration.add_argument("--benchmark")
    actions = commands.add_parser("actions", help="List validated pass actions")
    actions.add_argument("--include-unavailable", action="store_true")
    tune = commands.add_parser("tune", help="Run a bounded tuning experiment")
    tune.add_argument("--benchmark", required=True)
    tune.add_argument("--mode", choices=("quick", "full"), default="quick")
    tune.add_argument("--budget", type=int)
    tune.add_argument("--size", type=int)
    tune.add_argument("--seed", type=int)
    tune.add_argument("--repeat", type=int)
    tune.add_argument("--llm-mode", choices=("auto", "off", "openai"))
    for name in ("status", "trials", "report", "cancel", "retry"):
        command = commands.add_parser(name)
        command.add_argument("experiment_id")
        if name == "trials":
            command.add_argument("--strategy", choices=("guided", "random"))
        if name == "report":
            command.add_argument("--output-dir", type=Path)
    serve = commands.add_parser("serve", help="Start the local API and webpage")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "llm-status":
            from .llm import OpenAISettings
            print(json.dumps(OpenAISettings.from_env().public_status(), indent=2))
            return 0
        if args.command == "serve":
            if args.host != "127.0.0.1":
                raise ValueError("Only 127.0.0.1 is supported by the local service")
            try:
                import uvicorn
            except ImportError as exc:
                raise RuntimeError("Install the web extra: pip install -e '.[web]'") from exc
            uvicorn.run("autotuner.api:app", host=args.host, port=args.port, workers=1, reload=False)
            return 0
        service = TuningService(reconcile=True)
        if args.command == "doctor":
            result = service.preflight()
        elif args.command == "benchmarks":
            result = service.list_benchmarks()
        elif args.command == "calibrate":
            from .calibration import calibrate
            result = calibrate(args.benchmark)
        elif args.command == "actions":
            result = service.list_actions(args.include_unavailable)
        elif args.command == "tune":
            request = {"benchmark_id": args.benchmark, "mode": args.mode}
            if args.llm_mode is not None:
                request["llm_mode"] = args.llm_mode
            if args.budget is not None:
                request["budget_per_strategy"] = args.budget
            overrides = {name: getattr(args, name) for name in ("size", "seed", "repeat") if getattr(args, name) is not None}
            if overrides:
                from .registry import benchmark
                defaults = benchmark(args.benchmark)["quick_input" if args.mode == "quick" else "tuning_input"]
                request["tuning_input"] = {**defaults, **overrides}
            experiment_id = service.create_experiment(request)
            print(f"Experiment {experiment_id} started", flush=True)
            result = service.run_experiment(experiment_id)
            print(f"Saved report: {experiment_id}", flush=True)
        elif args.command == "cancel":
            result = service.cancel_experiment(args.experiment_id)
        elif args.command == "retry":
            experiment_id = service.retry_experiment(args.experiment_id)
            print(f"Retry experiment {experiment_id} started", flush=True)
            result = service.run_experiment(experiment_id)
        elif args.command == "status":
            result = service.get_experiment(args.experiment_id)
            if result is None:
                raise KeyError(args.experiment_id)
        elif args.command == "trials":
            if service.get_experiment(args.experiment_id) is None:
                raise KeyError(args.experiment_id)
            result = service.list_trials(args.experiment_id, strategy=args.strategy, limit=100)
        else:
            result = service.report(args.experiment_id)
            if args.output_dir:
                from .config import RUNS_DIR
                import shutil
                args.output_dir.mkdir(parents=True, exist_ok=True)
                for name in result["artifacts"]:
                    shutil.copy2(RUNS_DIR / args.experiment_id / name, args.output_dir / name)
        print(json.dumps(result, indent=2, default=str))
        return 0
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.SubprocessError, TrialFailure, KeyboardInterrupt) as exc:
        print(f"error: {'Interrupted by user' if isinstance(exc, KeyboardInterrupt) else exc}", file=sys.stderr)
        return 130 if isinstance(exc, (ExperimentCancelled, KeyboardInterrupt)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
