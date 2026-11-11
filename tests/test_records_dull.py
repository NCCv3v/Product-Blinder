from product_blinder.records import (
    dull_change_index,
    dull_change_view,
    format_index,
    record_snapshot,
)
from product_blinder.store import Store


def test_dull_index_formula_cases():
    assert dull_change_index(1.0, 0.40625) == 59.375
    assert format_index(59.375) == "59.4"
    assert dull_change_index(1.0, 1.0) == 0
    assert format_index(0) == "0"
    assert dull_change_index(0.4, 1.0) == 0
    assert dull_change_index(None, 1.0) is None
    assert dull_change_index(1.0, None) is None
    assert dull_change_index(1.0, 0.4, comparable=False) is None
    assert format_index(None) == "N/A"
    # Zero baseline cannot invent a relative dullness percentage from nothing.
    assert dull_change_index(0.0, 0.0) == 0


def test_save_list_reload_record_without_secrets(tmp_path):
    store = Store(tmp_path / "records.sqlite")
    batch = {
        "id": "batch-stable",
        "status": "complete",
        "suite_hash": "abc",
        "batch_scoring_hash": "score-1",
        "suite": {"name": "demo"},
        "repetitions": 5,
        "aggregate": {"score_mean": 1.0, "complete_repetitions": 5, "output_consistency_mean": 1.0},
        "analysis": {
            "quality": {"display": "N/A → 100%"},
            "mode_label": "Local heuristic testers — no paid model ran",
        },
        "problems": [],
        "source": {"synthetic": True, "api_key": "sk-secret-should-not-leak"},
    }
    snapshot = record_snapshot(batch, "Stable baseline")
    assert snapshot["name"] == "Stable baseline"
    assert snapshot["score_mean"] == 1.0
    assert snapshot["batch_id"] == "batch-stable"
    assert "sk-secret" not in str(snapshot)
    assert snapshot["source"]["api_key"] == "[REDACTED]"
    store.save_record(snapshot)
    listed = store.list_records()
    assert len(listed) == 1 and listed[0]["id"] == snapshot["id"]
    loaded = store.get_record(snapshot["id"])
    assert loaded["name"] == "Stable baseline"
    assert loaded["suite_hash"] == "abc"
    assert "sk-secret" not in str(loaded)


def test_dull_change_view_improved_unchanged_and_incomparable():
    record = {
        "id": "r1",
        "name": "Older stable",
        "created": "2026-11-11T16:00:00+08:00",
        "suite_hash": "abc",
        "batch_scoring_hash": "score-1",
        "score_mean": 1.0,
        "problems": [],
    }
    current = {
        "suite_hash": "abc",
        "batch_scoring_hash": "score-1",
        "aggregate": {"score_mean": 0.40625},
        "problems": [{"test_cases": ["policy-1-typo-1"], "failed_checks": ["accuracy"]}],
    }
    view = dull_change_view(record, current)
    assert view["title"] == "How Dull changed 指數"
    assert view["index"] == 59.375
    assert view["display"] == "59.4"
    assert view["scores_display"].startswith("100%")
    assert "40.6%" in view["scores_display"]
    assert view["improved"] is False

    better = dict(current)
    better["aggregate"] = {"score_mean": 1.0}
    record_low = dict(record, score_mean=0.4)
    improved = dull_change_view(record_low, better)
    assert improved["index"] == 0 and improved["improved"] is True
    assert improved["display"] == "0"
    assert "40%" in improved["scores_display"] and "100%" in improved["scores_display"]
    assert "did not get duller" in improved["reason"]

    mismatch = dull_change_view(record, dict(current, suite_hash="other"))
    assert mismatch["index"] is None and mismatch["display"] == "N/A"
    assert "N/A" in mismatch["reason"]


def test_dull_change_view_without_record_is_prompt_not_unmatched():
    batch = {
        "suite_hash": "abc",
        "batch_scoring_hash": "s1",
        "aggregate": {"score_mean": 0.4},
        "problems": [{"test_cases": ["fair-0-man"]}],
    }
    view = dull_change_view(None, batch)
    assert view["display"] == "N/A"
    assert view["index"] is None
    assert "Select a saved record" in view["reason"]
    assert "not a matched suite" not in view["reason"]
    assert view["bullets"] == []
