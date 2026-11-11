"""Offline HTML/JSON/CSV exports and static charts. HTML always escapes target data."""

import csv
import html
import io
import json
from pathlib import Path


def csv_text(run):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["case_id", "category", "score", "latency_ms", "error", "output"])
    for t in run["traces"]:
        row = [t[k] for k in ("case_id", "category", "score", "latency_ms", "error", "output")]
        # Prevent spreadsheet formula injection when importing report CSV.
        writer.writerow(
            ["'" + str(x) if isinstance(x, str) and x.startswith(("=", "+", "-", "@")) else x for x in row]
        )
    return stream.getvalue()


def html_text(run):
    def esc(x):
        return html.escape(str(x))

    comparison = run["comparison"]
    rows = "".join(
        f"<tr><td>{esc(t['case_id'])}</td><td>{esc('N/A' if t['score'] is None else round(t['score'], 3))}</td><td>{t['latency_ms']:.2f}</td>"
        f"<td>{esc(t['error'] or '—')}</td><td><details><summary>Trace</summary><pre>"
        f"{esc(json.dumps(t, indent=2, ensure_ascii=False))}</pre></details></td></tr>"
        for t in run["traces"]
    )
    metrics = "".join(
        f"<li>{esc(k)}: {esc('N/A' if v is None else round(v, 3))} "
        f"(n={run['summary']['sample_sizes'][k]})</li>"
        for k, v in run["summary"]["per_evaluator"].items()
    )
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Product Blinder — Evaluation report</title><style>
body{{font:16px system-ui;background:#0b1222;color:#e6edf7;max-width:1150px;margin:40px auto;padding:20px}}
h1{{color:#70e2c1}}small{{color:#a7b6cb}}table{{width:100%;border-collapse:collapse}}td,th{{border-bottom:1px solid #31405c;padding:12px;text-align:left}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;max-width:750px}}.badge{{background:#253c57;border-radius:16px;padding:8px 16px;display:inline-block}}
</style><h1>Product Blinder</h1><small>LOCAL EVALUATION / {esc(run["created"])} (Hong Kong time)</small>
<h2 class="badge">{esc(comparison["verdict"]).upper()}</h2><p>Run {esc(run["id"])} · Synthetic: {esc(run["metadata"]["synthetic"])}
· Mean case score {esc("N/A" if run["summary"]["score"] is None else round(run["summary"]["score"], 3))}</p><p>Suite SHA-256: {esc(run["suite_hash"])}</p>
<p>{esc(comparison.get("reason"))}</p><p>Paired cases: {comparison["paired_cases"]} · Independent families: {comparison["independent_units"]}
· Delta: {esc(comparison["delta"])} · 95% CI: {esc(comparison["ci95"])}</p>
<p>{esc(comparison["warning"])} Scores are not a causal explanation. Missing usage/cost is unknown, not zero.</p>
<ul>{metrics}</ul><h2>Case traces</h2><table><tr><th>Case</th><th>Score</th><th>Latency ms</th><th>Error</th><th>Details</th></tr>{rows}</table>
<h2>Augment and rerun</h2><p>{esc(" ".join(run["suggestions"]))}</p>
<h2>Frozen metadata</h2><pre>{esc(json.dumps({k: run[k] for k in ("target", "config", "metadata")}, indent=2))}</pre></html>"""


def export_run(run, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False))
    (directory / "cases.csv").write_text(csv_text(run))
    (directory / "report.html").write_text(html_text(run))


def charts(baseline, candidate, directory):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    plt.style.use("dark_background")
    fig, axes = plt.subplots(2, 2, figsize=(15, 10), facecolor="#0b1222")
    for ax in axes.flat:
        ax.set_facecolor("#111e32")
        ax.spines[["top", "right"]].set_visible(False)
    colors = ["#70e2c1", "#ff7d88"]
    scores = [r["summary"]["score"] for r in (baseline, candidate)]
    axes[0, 0].plot([1, 2], scores, "o-", color=colors[0], linewidth=3)
    axes[0, 0].set(
        xticks=[1, 2],
        xticklabels=["Baseline", "Degraded"],
        ylim=(0, 1.1),
        title="Mean case score · two actual synthetic rounds",
    )
    roles = list(baseline["summary"]["per_evaluator"])
    for i, run in enumerate((baseline, candidate)):
        axes[0, 1].barh(
            [x + i * 0.35 for x in range(len(roles))],
            [
                float("nan")
                if run["summary"]["per_evaluator"][r] is None
                else run["summary"]["per_evaluator"][r]
                for r in roles
            ],
            height=0.35,
            label=["Baseline", "Degraded"][i],
            color=colors[i],
        )
    axes[0, 1].set(
        yticks=[x + 0.175 for x in range(len(roles))],
        yticklabels=roles,
        title="Evaluator breakdown",
        xlim=(0, 1.1),
    )
    axes[0, 1].legend()
    axes[1, 0].scatter(
        [t["score"] for t in baseline["traces"]],
        [t["score"] for t in candidate["traces"]],
        color=colors[1],
        alpha=0.65,
    )
    axes[1, 0].plot([0, 1], [0, 1], "--", color="#7186a2")
    axes[1, 0].set(
        xlabel="Baseline score",
        ylabel="Candidate score",
        title="Paired case comparison",
        xlim=(-0.05, 1.05),
        ylim=(-0.05, 1.05),
    )
    axes[1, 1].boxplot(
        [[t["latency_ms"] for t in r["traces"]] for r in (baseline, candidate)],
        tick_labels=["Baseline", "Degraded"],
    )
    axes[1, 1].set(ylabel="Measured latency (ms)", title="Synthetic workflow latency distribution")
    fig.suptitle("PRODUCT BLINDER / Synthetic regression demonstration", fontsize=20, color=colors[0])
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(directory / "evaluation-charts.png", dpi=160)
    plt.close(fig)
