"""Batch JSON/CSV exports and PNG plots. No HTML UI or browser dependency."""

import csv
import io
import json
from pathlib import Path

from .analysis import analysis_markdown


def repetition_rows(batch):
    by_index = {r["repetition"]: r for r in batch["runs"]}
    rows = []
    for entry in batch["repetition_records"]:
        run = by_index.get(entry["index"])
        p = run["performance"] if run else {}
        s = run["summary"] if run else {}
        rows.append(
            {
                "batch_id": batch["id"],
                "repetition": entry["index"],
                "status": entry["status"],
                "run_id": entry["run_id"],
                "score": s.get("score"),
                "duration_s": p.get("duration_s"),
                "latency_mean_ms": p.get("latency_ms_mean"),
                "latency_p50_ms": p.get("latency_ms_p50"),
                "latency_p95_ms": p.get("latency_ms_p95"),
                "throughput_cases_s": p.get("throughput_cases_s"),
                "success_rate": p.get("target_success_rate"),
                "error_rate": p.get("target_error_rate"),
                "timeout_rate": p.get("timeout_rate"),
                "source_adapter": batch["source"]["adapter"],
                "source_model": batch["source"]["model"],
                "started": entry["started"],
                "recorded_seed": entry["recorded_seed"],
                "provider_seed_applied": False,
                "suite_hash": batch["suite_hash"],
                "tokens": s.get("tokens"),
                "cost_usd": None,
                "ttft_ms": None,
                **{
                    r: s.get("per_evaluator", {}).get(r)
                    for r in (
                        "accuracy",
                        "groundedness",
                        "instructions",
                        "validity",
                        "robustness",
                        "fairness",
                        "safety",
                        "reliability",
                    )
                },
            }
        )
    return rows


def batch_csv(batch):
    rows = repetition_rows(batch)
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v
                for k, v in row.items()
            }
        )
    return stream.getvalue()


def export_batch(batch, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "batch.json").write_text(json.dumps(batch, indent=2, ensure_ascii=False))
    (directory / "repetitions.csv").write_text(batch_csv(batch))
    # Missing values stay empty/JSON null, never fabricated numeric zero.
    (directory / "case-means.json").write_text(json.dumps(batch["aggregate"]["traces"], indent=2))
    (directory / "test-cases.json").write_text(
        json.dumps(batch.get("test_cases") or [], indent=2, ensure_ascii=False)
    )
    (directory / "problems.json").write_text(
        json.dumps(batch.get("problems") or [], indent=2, ensure_ascii=False)
    )
    report = batch.get("analysis") or {}
    (directory / "analysis.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    (directory / "analysis.md").write_text(analysis_markdown(report, batch) if report else "")
    rows = batch.get("test_cases") or []
    if rows:
        stream = io.StringIO()
        writer = csv.DictWriter(
            stream,
            fieldnames=["label", "id", "family", "check", "expected", "category", "source", "agents"],
        )
        writer.writeheader()
        for item in rows:
            writer.writerow(
                {
                    "label": "Test Case",
                    "id": item["id"],
                    "family": item.get("family"),
                    "check": item.get("check"),
                    "expected": item.get("expected") or "",
                    "category": item.get("category"),
                    "source": item.get("source"),
                    "agents": ", ".join(item.get("agents") or []),
                }
            )
        (directory / "test-cases.csv").write_text(stream.getvalue())
    problems = batch.get("problems") or []
    if problems:
        stream = io.StringIO()
        writer = csv.DictWriter(
            stream,
            fieldnames=["problem", "severity", "test_cases", "failed_checks", "failed_of_five", "evidence"],
        )
        writer.writeheader()
        for item in problems:
            writer.writerow(
                {
                    "problem": item["problem"],
                    "severity": item["severity"],
                    "test_cases": ", ".join(item["test_cases"]),
                    "failed_checks": ", ".join(item["failed_checks"]),
                    "failed_of_five": item["failed_of_five"],
                    "evidence": item["evidence"],
                }
            )
        (directory / "problems.csv").write_text(stream.getvalue())


def batch_charts(baseline, candidate, directory):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    plt.style.use("dark_background")
    fig, axes = plt.subplots(2, 2, figsize=(15, 10), facecolor="#0b1222")
    for ax in axes.flat:
        ax.set_facecolor("#111e32")
        ax.spines[["top", "right"]].set_visible(False)
    colors = ["#70e2c1", "#ff8c99"]
    for batch, color, label in [
        (baseline, colors[0], "Stable batch"),
        (candidate, colors[1], "Degraded batch"),
    ]:
        runs = batch["runs"]
        x = [r["repetition"] for r in runs]
        axes[0, 0].plot(
            x,
            [r["summary"]["score"] if r["status"] == "complete" else np.nan for r in runs],
            "o-",
            color=color,
            label=label,
        )
        axes[0, 1].plot(x, [r["performance"]["duration_s"] for r in runs], "o-", color=color, label=label)
        axes[1, 1].plot(x, [r["performance"]["latency_ms_p95"] for r in runs], "o-", color=color, label=label)
    axes[0, 0].set(
        title="Five complete repetitions per batch: actual scores",
        xlabel="Repetition",
        ylabel="Mean case score",
        ylim=(-0.05, 1.05),
        xticks=[1, 2, 3, 4, 5],
    )
    axes[0, 0].legend()
    axes[0, 1].set(
        title="Measured end-to-end evaluation duration", xlabel="Repetition", ylabel="Seconds: target + judge"
    )
    roles = list(baseline["aggregate"]["summary"]["per_evaluator"])
    for i, batch in enumerate((baseline, candidate)):
        axes[1, 0].barh(
            [j + i * 0.35 for j in range(len(roles))],
            [
                np.nan
                if batch["aggregate"]["summary"]["per_evaluator"][r] is None
                else batch["aggregate"]["summary"]["per_evaluator"][r]
                for r in roles
            ],
            height=0.35,
            color=colors[i],
        )
    axes[1, 0].set(
        yticks=[j + 0.175 for j in range(len(roles))],
        yticklabels=roles,
        title="Evaluator means: five repeats, missing = N/A",
        xlim=(0, 1.05),
    )
    axes[1, 1].set(
        title="Measured target latency P95 by repetition", xlabel="Repetition", ylabel="Milliseconds"
    )
    axes[1, 1].legend()
    fig.suptitle(
        "PRODUCT BLINDER v0.2 / Two synthetic batches = 10 underlying runs", color=colors[0], fontsize=18
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fig.savefig(directory / "five-repetition-charts.png", dpi=160)
    plt.close(fig)
