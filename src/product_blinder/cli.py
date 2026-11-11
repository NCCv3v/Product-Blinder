"""CLI and CI gate. Exit codes: 0 stable/improved/baseline; 2 regression; 3 insufficient; 1 error."""

import argparse
import asyncio
import json
import os
import sys
from importlib.resources import files
from pathlib import Path

from .batches import run_batch
from .batch_reports import export_batch, batch_charts
from .models import RunConfig, TargetConfig, load_suite
from .reports import charts, export_run
from .runner import run_suite
from .security import Secrets
from .store import Store


def parser():
    p = argparse.ArgumentParser(prog="product-blinder")
    p.add_argument("--db", default="data/product-blinder.sqlite")
    sub = p.add_subparsers(dest="action", required=True)
    r = sub.add_parser("run", help="Evaluate a frozen JSON/YAML suite")
    r.add_argument("--suite", required=True)
    r.add_argument("--target", help="TargetConfig JSON file; defaults to synthetic fixture")
    r.add_argument("--config", help="RunConfig JSON file (no credentials)")
    r.add_argument("--fixture", choices=["stable", "degraded"], default="stable")
    r.add_argument(
        "--repetitions",
        type=int,
        default=5,
        help="Full suite repetitions; default 5. Use 1 for explicit single-run mode.",
    )
    r.add_argument("--baseline", action="store_true", help="Explicitly set this run as baseline")
    r.add_argument("--consent-paid", action="store_true")
    r.add_argument("--allow-exec", action="store_true")
    r.add_argument("--out", default="reports/latest")
    g = sub.add_parser("gate", help="CI exit status from a saved run; performs no new evaluation")
    g.add_argument("run_id")
    b = sub.add_parser("baseline")
    b.add_argument("run_id")
    e = sub.add_parser("export")
    e.add_argument("run_id")
    e.add_argument("--out", required=True)
    sub.add_parser("history")
    sub.add_parser("ui", help="Start native desktop program; no browser")
    demo5 = sub.add_parser(
        "demo-five", help="Fresh five-repeat stable batch + five-repeat degraded batch (10 runs)"
    )
    demo5.add_argument("--suite")
    demo5.add_argument("--out", default="reports/v0.7/demo-five")
    bb = sub.add_parser("batch-baseline")
    bb.add_argument("batch_id")
    bg = sub.add_parser("batch-gate")
    bg.add_argument("batch_id")
    be = sub.add_parser("batch-export")
    be.add_argument("batch_id")
    be.add_argument("--out", required=True)
    d = sub.add_parser("demo", help="Exactly two synthetic rounds against one frozen suite")
    d.add_argument("--suite", default="examples/demo-suite.yaml")
    d.add_argument("--out", default="reports/demo")
    return p


def main():
    args = parser().parse_args()
    if args.action == "ui":
        from .desktop import main as desktop_main

        return desktop_main(["--db", args.db])
    try:
        store = Store(args.db)
        if args.action == "batch-baseline":
            store.set_batch_baseline(args.batch_id)
        elif args.action == "batch-gate":
            result = store.get_batch(args.batch_id)["comparison"]
            print(json.dumps(result, indent=2))
            return {"regression": 2, "insufficient-data": 3}.get(result["verdict"], 0)
        elif args.action == "batch-export":
            export_batch(store.get_batch(args.batch_id), args.out)
        elif args.action == "demo-five":
            if store.history() or store.batches():
                raise ValueError("demo-five requires a fresh database to prove ten new runs")
            suite = load_suite(
                Path(args.suite).read_text()
                if args.suite
                else files("product_blinder").joinpath("data/demo-suite.yaml").read_text()
            )
            config = RunConfig(max_calls=1000, max_judge_calls=1000)
            baseline = asyncio.run(
                run_batch(
                    suite,
                    TargetConfig(fixture="stable", model="synthetic-stable", revision="v0.2-stable"),
                    config,
                    Secrets(),
                    store,
                )
            )
            store.set_batch_baseline(baseline["id"])
            candidate = asyncio.run(
                run_batch(
                    suite,
                    TargetConfig(fixture="degraded", model="synthetic-degraded", revision="v0.2-degraded"),
                    config,
                    Secrets(),
                    store,
                )
            )
            export_batch(baseline, Path(args.out) / "stable-batch")
            export_batch(candidate, Path(args.out) / "degraded-batch")
            batch_charts(baseline, candidate, args.out)
            print(
                json.dumps(
                    {
                        "batch_count": 2,
                        "underlying_runs": 10,
                        "baseline_id": baseline["id"],
                        "candidate_id": candidate["id"],
                        "baseline_score": baseline["aggregate"]["score_mean"],
                        "candidate_score": candidate["aggregate"]["score_mean"],
                        "comparison": candidate["comparison"],
                    },
                    indent=2,
                )
            )
        elif args.action == "history":
            print(
                json.dumps(
                    [{k: r[k] for k in ("id", "created", "summary", "comparison")} for r in store.history()],
                    indent=2,
                )
            )
        elif args.action == "gate":
            result = store.get(args.run_id)["comparison"]
            print(json.dumps(result, indent=2))
            return {"regression": 2, "insufficient-data": 3}.get(result["verdict"], 0)
        elif args.action == "baseline":
            store.set_baseline(args.run_id)
            print("Baseline selected:", args.run_id)
        elif args.action == "export":
            export_run(store.get(args.run_id), args.out)
        elif args.action == "run":
            suite = load_suite(Path(args.suite).read_text())
            target = (
                TargetConfig.model_validate_json(Path(args.target).read_text())
                if args.target
                else TargetConfig(fixture=args.fixture)
            )
            config = (
                RunConfig.model_validate_json(Path(args.config).read_text()) if args.config else RunConfig()
            )
            if args.repetitions != 1:
                result = asyncio.run(
                    run_batch(
                        suite,
                        target,
                        config,
                        Secrets(os.getenv("PB_TARGET_API_KEY", ""), os.getenv("PB_JUDGE_API_KEY", "")),
                        store,
                        repetitions=args.repetitions,
                        consent=args.consent_paid,
                        allow_exec=args.allow_exec,
                    )
                )
                if args.baseline:
                    store.set_batch_baseline(result["id"])
                export_batch(result, args.out)
                print(
                    json.dumps(
                        {
                            "id": result["id"],
                            "status": result["status"],
                            "aggregate": result["aggregate"],
                            "comparison": result["comparison"],
                        },
                        indent=2,
                    )
                )
                return (
                    0
                    if args.baseline
                    else {"regression": 2, "insufficient-data": 3}.get(result["comparison"]["verdict"], 0)
                )
            result = asyncio.run(
                run_suite(
                    suite,
                    target,
                    config,
                    Secrets(os.getenv("PB_TARGET_API_KEY", ""), os.getenv("PB_JUDGE_API_KEY", "")),
                    store,
                    consent=args.consent_paid,
                    allow_exec=args.allow_exec,
                )
            )
            if args.baseline:
                store.set_baseline(result["id"])
            export_run(result, args.out)
            print(
                json.dumps(
                    {"id": result["id"], "summary": result["summary"], "comparison": result["comparison"]},
                    indent=2,
                )
            )
            if args.baseline:
                return 0
            return {"regression": 2, "insufficient-data": 3}.get(result["comparison"]["verdict"], 0)
        elif args.action == "demo":
            # Refuse to overwrite artifacts or add extra rounds to an existing demo DB.
            if store.history():
                raise ValueError(
                    "Demo needs a fresh DB to guarantee exactly two rounds. Choose --db <new-path>."
                )
            suite = load_suite(Path(args.suite).read_text())
            config = RunConfig()
            first = asyncio.run(
                run_suite(
                    suite,
                    TargetConfig(fixture="stable", model="synthetic-stable", revision="v1"),
                    config,
                    Secrets(),
                    store,
                )
            )
            store.set_baseline(first["id"])
            second = asyncio.run(
                run_suite(
                    suite,
                    TargetConfig(fixture="degraded", model="synthetic-degraded", revision="v2"),
                    config,
                    Secrets(),
                    store,
                )
            )
            export_run(first, Path(args.out) / "round-1")
            export_run(second, Path(args.out) / "round-2")
            charts(first, second, args.out)
            print(
                json.dumps(
                    {
                        "baseline_id": first["id"],
                        "candidate_id": second["id"],
                        "baseline_score": first["summary"]["score"],
                        "candidate_score": second["summary"]["score"],
                        "comparison": second["comparison"],
                    },
                    indent=2,
                )
            )
    except Exception as exc:
        # Hide arbitrary exception messages from provider/child processes.
        print(
            f"Product Blinder error ({type(exc).__name__}): configuration, input or execution failed. "
            "Check README and validated settings; no credential-bearing traceback printed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
