# Contributing to Product Blinder v0.7

- Install `.[desktop,dev]` in a virtual environment; Python 3.11+.
- Native Qt Widgets is the primary UI. No browser/webview/HTML frontend or local web server.
- Preserve exactly two main tabs: Chatbot / Dashboard Report. Settings are a separate dialog.
- Default evaluation session = five FULL frozen-suite repetitions, not five cases.
- One budget/call ceiling spans all repetitions/retries; missing/cancelled data stays N/A.
- Keep paid provider tests out of CI. Use synthetic fixtures and mocked endpoints, explicitly labelled.
- Run `QT_QPA_PLATFORM=offscreen pytest -q` and `ruff check src tests`.
- GUI tests must cleanly dispose native widget trees before QApplication teardown.
- Never commit keys, `.env`, databases, PII or private traces. Do not automatically publish reports.
- Evaluator/weight/prompt meaning changes must invalidate scoring/baseline compatibility.
- Group correlated cases for bootstrap inference; repetitions are not independent case families.
- Programs require explicit opt-in and are not sandboxed. Suite imports must remain data-only.
- Review native runtime licenses/corresponding-source obligations before publicly distributing binaries.
- No automatic training/release or assertions about provider compute allocation.
