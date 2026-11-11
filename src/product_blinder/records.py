"""Local saved analysis records and the How Dull changed 指數.

Dull change index formula (documented for the UI note as well):

    index = 100 * max(0, S_old - S_now) / max(S_old, 1e-9)

S_old and S_now are mean quality scores in [0, 1] from a saved record and the
current analysis. If either score is missing, or the suites/scoring config are
not comparable, the index is None and the UI shows N/A — nothing is invented.
An unchanged score yields 0. An improvement also yields 0; the UI must still
show the familiar Record → Current percentages and must not claim it got
duller. The index is “how much duller,” not a causal explanation and not a
claim about vendor compute.
"""

from datetime import datetime
import uuid
from zoneinfo import ZoneInfo

from .scores import score_transition
from .security import Secrets

EPS = 1e-9


def now():
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat()


def dull_change_index(s_old, s_now, comparable=True):
    if not comparable or s_old is None or s_now is None:
        return None
    try:
        s_old = float(s_old)
        s_now = float(s_now)
    except (TypeError, ValueError):
        return None
    if not (0 <= s_old <= 1 and 0 <= s_now <= 1):
        return None
    return 100.0 * max(0.0, s_old - s_now) / max(s_old, EPS)


def suites_comparable(record, batch):
    return bool(
        record
        and batch
        and record.get("suite_hash")
        and record["suite_hash"] == batch.get("suite_hash")
        and record.get("batch_scoring_hash") == batch.get("batch_scoring_hash")
    )


def format_index(index):
    if index is None:
        return "N/A"
    if index == 0:
        return "0"
    return f"{index:.1f}".rstrip("0").rstrip(".")


def record_snapshot(batch, name=""):
    """Compact, secret-free snapshot for the local records table."""
    aggregate = batch.get("aggregate") or {}
    analysis = batch.get("analysis") or {}
    problems = [
        {
            "problem": item.get("problem"),
            "severity": item.get("severity"),
            "test_cases": item.get("test_cases"),
            "failed_checks": item.get("failed_checks"),
            "failed_of_five": item.get("failed_of_five"),
        }
        for item in (batch.get("problems") or [])[:12]
    ]
    snapshot = {
        "id": uuid.uuid4().hex[:12],
        "name": (name or "").strip() or f"Record {batch.get('id')}",
        "created": now(),
        "batch_id": batch.get("id"),
        "suite_hash": batch.get("suite_hash"),
        "batch_scoring_hash": batch.get("batch_scoring_hash"),
        "suite_name": (batch.get("suite") or {}).get("name"),
        "status": batch.get("status"),
        "score_mean": aggregate.get("score_mean"),
        "complete_repetitions": aggregate.get("complete_repetitions"),
        "expected_repetitions": batch.get("repetitions") or aggregate.get("expected_repetitions"),
        "consistency": aggregate.get("output_consistency_mean"),
        "analysis": analysis,
        "problems": problems,
        "source": batch.get("source"),
    }
    return Secrets().redact(snapshot)


def dull_change_view(record, batch):
    comparable = suites_comparable(record, batch)
    s_old = None if record is None else record.get("score_mean")
    s_now = (batch.get("aggregate") or {}).get("score_mean") if batch else None
    index = dull_change_index(s_old, s_now, comparable)
    improved = comparable and s_old is not None and s_now is not None and s_now > s_old
    unchanged = comparable and s_old is not None and s_now is not None and s_old == s_now
    reason = (
        "Measured how much duller the current mean quality is versus the saved record. Not a causal claim."
    )
    if record is None:
        reason = "Select a saved record to compare with the current analysis report."
    elif not comparable:
        reason = "N/A — the saved record and current report are not a matched suite/scoring pair."
    elif s_old is None or s_now is None:
        reason = "N/A — a mean quality score is missing, so no index is computed."
    elif improved:
        reason = "Quality improved versus the saved record. The index is 0; it did not get duller."
    elif unchanged:
        reason = "Quality is unchanged versus the saved record. The index is 0."
    bullets = []
    if record and (record.get("problems") or analysis_failing(record)):
        failing = [", ".join(item.get("test_cases") or []) for item in record.get("problems") or []]
        failing = [item for item in failing if item][:3]
        if failing:
            bullets.append("Record top failing Test Cases: " + "; ".join(failing))
    if record and batch:
        now_failing = [", ".join(item.get("test_cases") or []) for item in (batch.get("problems") or [])[:3]]
        now_failing = [item for item in now_failing if item]
        if now_failing:
            bullets.append("Current top failing Test Cases: " + "; ".join(now_failing))
        elif improved or unchanged:
            bullets.append("Current report has no evidenced Find out problem rows.")
    return {
        "title": "How Dull changed 指數",
        "index": index,
        "display": format_index(index),
        "s_old": s_old,
        "s_now": s_now,
        "scores_display": score_transition(s_old, s_now),
        "improved": improved,
        "unchanged": unchanged,
        "comparable": comparable,
        "record_name": None if record is None else record.get("name"),
        "record_created": None if record is None else record.get("created"),
        "record_id": None if record is None else record.get("id"),
        "reason": reason,
        "bullets": bullets[:2],
    }


def analysis_failing(record):
    return ((record.get("analysis") or {}).get("top_failing_test_cases")) or []
