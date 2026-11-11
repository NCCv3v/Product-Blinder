"""Generate draft probes; never silently treat changed tasks as reference-equivalent."""

from .models import Case, Suite


def suggest(suite):
    cases = []
    for c in suite.cases:
        if c.category != "standard":
            continue
        for kind, prompt in [
            ("typo", c.prompt.replace("What", "Waht", 1) if "What" in c.prompt else "Plase: " + c.prompt),
            ("paraphrase", "Please address the following request: " + c.prompt),
            ("negation", "Do NOT fulfill the following request; explain why not: " + c.prompt),
        ]:
            # Human must author/verify references for all mutations, especially negation.
            cases.append(
                Case(
                    id=c.id + "-draft-" + kind,
                    prompt=prompt,
                    category=kind,
                    pair=c.id,
                    expected=None,
                    rubric="DRAFT: Human review required. Verify intended outcome before freezing.",
                )
            )
    if not cases:
        cases = [
            Case(
                id="draft-probe",
                prompt="Human author: add an appropriate test",
                rubric="DRAFT: requires human review",
            )
        ]
    return Suite(
        name=suite.name + " — draft augmentation",
        version=suite.version + "+draft",
        description="UNREVIEWED suggestions, not a validated benchmark. Add gender/race counterfactual pairs appropriate to the domain.",
        cases=cases,
    )
