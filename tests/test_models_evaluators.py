import json

import pytest

from product_blinder.augmentation import suggest
from product_blinder.evaluators import apply_fairness, deterministic
from product_blinder.models import Case, RunConfig, Suite, load_suite
from product_blinder.security import Secrets, check_no_embedded_secrets, validate_url


def test_suite_hash_is_frozen_and_order_sensitive():
    a = Suite(name="a", cases=[Case(id="a", prompt="Q", expected="A")])
    assert a.digest == load_suite(a.model_dump_json(by_alias=True)).digest
    assert a.digest != a.model_copy(update={"version": "2"}).digest
    assert a.digest != Suite(name="a", cases=[Case(id="a", prompt="Q", expected="B")]).digest


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError):
        Suite(name="x", cases=[Case(id="same", prompt="a"), Case(id="same", prompt="b")])


@pytest.mark.parametrize(
    "text", ["!!python/object/apply:os.system ['echo BAD']", "[]", '{"name":"x","cases":[]}']
)
def test_invalid_or_unsafe_yaml_rejected(text):
    with pytest.raises(Exception):
        load_suite(text)


def test_unknown_fields_rejected():
    with pytest.raises(ValueError):
        load_suite('{"name":"a","cases":[{"id":"a","prompt":"x","execute":"oops"}]}')


def test_oversized_suite_rejected():
    with pytest.raises(ValueError):
        load_suite("a" * 2_000_001)


def test_missing_evidence_reference_are_na():
    m = deterministic(Case(id="a", prompt="Q"), {"output": "Anything", "latency_ms": 2})
    assert m["accuracy"]["score"] is None
    assert m["groundedness"]["score"] is None
    assert m["validity"]["score"] == 1


def test_exact_instruction_groundedness_checks():
    c = Case(
        id="a",
        prompt="Q",
        expected="30 days",
        evidence=["Refunds: 30 days"],
        must_include=["30"],
        must_not_include=["forever"],
    )
    m = deterministic(c, {"output": "30 days", "latency_ms": 2})
    assert all(
        m[r]["score"] == 1 for r in ("accuracy", "groundedness", "instructions", "validity", "reliability")
    )


@pytest.mark.parametrize("output,score", [('{"ok":true}', 1), ('{"ok":false}', 0), ("oops", 0)])
def test_schema_check(output, score):
    c = Case(
        id="a",
        prompt="Q",
        schema={"type": "object", "properties": {"ok": {"const": True}}, "required": ["ok"]},
    )
    assert deterministic(c, {"output": output, "latency_ms": 2})["validity"]["score"] == score


def test_injection_and_latency_fail():
    c = Case(id="a", prompt="Q", category="injection", must_not_include=["PWNED"], max_latency_ms=1)
    m = deterministic(c, {"output": "PWNED", "latency_ms": 2})
    assert m["safety"]["score"] == 0
    assert m["reliability"]["score"] == 0


def test_fairness_not_rewarding_equally_wrong():
    cases = [
        Case(id=x, prompt=x, expected="Qualified", category="fairness", pair="gender")
        for x in ("man", "woman")
    ]
    traces = [
        {
            "case_id": c.id,
            "output": "Reject",
            "metrics": deterministic(c, {"output": "Reject", "latency_ms": 1}),
        }
        for c in cases
    ]
    apply_fairness(cases, traces)
    assert all(t["metrics"]["fairness"]["score"] == 0 for t in traces)


def test_incomplete_fairness_is_na():
    c = Case(id="a", prompt="Q", category="fairness", pair="missing")
    traces = [{"case_id": "a", "output": "A", "metrics": deterministic(c, {"output": "A", "latency_ms": 1})}]
    apply_fairness([c], traces)
    assert traces[0]["metrics"]["fairness"]["score"] is None


def test_augmentation_does_not_blindly_inherit_negation_reference():
    suite = Suite(name="a", cases=[Case(id="a", prompt="What is allowed?", expected="Answer")])
    draft = suggest(suite)
    assert {c.category for c in draft.cases} == {"typo", "paraphrase", "negation"}
    assert all(c.expected is None for c in draft.cases)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://u:p@a.test/x",
        "https://a.test?api_key=x",
        "http://169.254.169.254/",
        "http://remote.test/api",
    ],
)
def test_unsafe_urls_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:9000/api", "https://example.test/api"])
def test_allowed_urls(url):
    assert validate_url(url) == url


def test_redacts_known_keys_and_secret_fields():
    secret = Secrets("target-example-key", "judge-example-key")
    result = secret.redact(
        {"output": "target-example-key judge-example-key", "api_key": "x", "total_tokens": 42}
    )
    assert "example-key" not in json.dumps(result)
    assert result["total_tokens"] == 42


def test_rejects_embedded_credentials():
    with pytest.raises(ValueError):
        check_no_embedded_secrets({"body": {"api_key": "foo"}}, Secrets())
    with pytest.raises(ValueError):
        check_no_embedded_secrets({"expected": "known-key"}, Secrets("known-key"))


def test_scoring_hash_judge_change_invalidates():
    assert (
        RunConfig(use_judge=True, judge_model="one").scoring_digest
        != RunConfig(use_judge=True, judge_model="two").scoring_digest
    )


def test_invalid_schema_is_input_error_not_product_failure():
    with pytest.raises(ValueError):
        Case(id="bad", prompt="Q", schema={"type": "nonsense-type"})
