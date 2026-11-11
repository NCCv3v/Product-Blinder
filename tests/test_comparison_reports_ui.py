import pytest

from product_blinder.comparison import compare
from product_blinder.reports import csv_text, html_text
from product_blinder.store import Store


def record(score=1, count=12):
    return {
        "id": "r",
        "suite_hash": "same",
        "scoring_hash": "same",
        "created": "2026-11-11T15:00:00+08:00",
        "suite": {"cases": []},
        "traces": [
            {
                "case_id": str(i),
                "family": None,
                "score": score,
                "output": "A",
                "category": "standard",
                "latency_ms": 2,
                "error": None,
                "metrics": {"accuracy": {"score": score, "method": "rule-v1"}},
            }
            for i in range(count)
        ],
    }


@pytest.mark.parametrize("score,verdict", [(0, "regression"), (1, "stable")])
def test_regression_and_stable(score, verdict):
    result = compare(record(1), record(score))
    assert result["verdict"] == verdict
    assert result["paired_cases"] == 12
    assert result["independent_units"] == 12


def test_improved():
    assert compare(record(0.1), record(0.9))["verdict"] == "improved"


def test_small_sample_insufficient():
    assert compare(record(count=2), record(0, count=2))["verdict"] == "insufficient-data"


def test_family_clustering_prevents_pseudo_replication():
    candidate = record(0)
    for t in candidate["traces"]:
        t["family"] = "one-correlated-family"
    assert compare(record(), candidate)["verdict"] == "insufficient-data"


def test_suite_or_judge_change_rejected():
    for key in ("suite_hash", "scoring_hash"):
        candidate = record(0)
        candidate[key] = "changed"
        assert compare(record(), candidate)["verdict"] == "insufficient-data"


def test_changed_coverage_rejected():
    candidate = record(0)
    candidate["traces"][0]["metrics"]["accuracy"]["score"] = None
    assert compare(record(), candidate)["verdict"] == "insufficient-data"


def test_uncertain_change_not_claimed_regression():
    baseline = record(0.5)
    candidate = record(0.5)
    for i, t in enumerate(candidate["traces"]):
        t["score"] = 1 if i < 6 else 0
    assert compare(baseline, candidate)["verdict"] == "insufficient-data"


def test_sqlite_persistence_and_baseline(tmp_path):
    store = Store(tmp_path / "history.sqlite")
    store.save(record())
    store.set_baseline("r")
    assert Store(tmp_path / "history.sqlite").baseline("same")["id"] == "r"
    assert len(store.history()) == 1
    with pytest.raises(ValueError):
        store.get("missing")


def test_csv_formula_injection_prevented():
    run = record()
    run["traces"][0]["output"] = "=SUM(1,2)"
    assert "'=SUM" in csv_text(run)


def test_html_escapes_untrusted_outputs():
    run = record()
    run.update(
        summary={"score": 1, "per_evaluator": {}, "sample_sizes": {}},
        metadata={"synthetic": True},
        target={},
        config={},
        suggestions=[],
    )
    run["comparison"] = compare(None, run)
    run["traces"][0]["output"] = '<script>alert("x")</script>'
    result = html_text(run)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


@pytest.mark.parametrize(
    "verdict,expected_exit", [("regression", 2), ("stable", 0), ("insufficient-data", 3)]
)
def test_cli_saved_run_gate_exit_codes(tmp_path, monkeypatch, verdict, expected_exit):
    from product_blinder.cli import main
    import sys

    path = tmp_path / "gate.sqlite"
    run = record()
    run["comparison"] = {"verdict": verdict}
    Store(path).save(run)
    monkeypatch.setattr(sys, "argv", ["product-blinder", "--db", str(path), "gate", "r"])
    assert main() == expected_exit


def test_html_reports_missing_score_as_na():
    run = record()
    run.update(
        summary={"score": None, "per_evaluator": {}, "sample_sizes": {}},
        metadata={"synthetic": False},
        target={},
        config={},
        suggestions=[],
    )
    run["comparison"] = compare(None, run)
    for t in run["traces"]:
        t["score"] = None
    assert "N/A" in html_text(run)
