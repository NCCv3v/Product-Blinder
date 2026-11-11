"""Format quality scores without importing the Qt UI."""


def score_percent(value):
    return "N/A" if value is None else f"{value * 100:.1f}".rstrip("0").rstrip(".") + "%"


def score_transition(baseline, current):
    return f"{score_percent(baseline)} → {score_percent(current)}"
