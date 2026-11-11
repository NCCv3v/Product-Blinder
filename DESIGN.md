# Product Blinder v0.7 native design

Implemented with PySide6 Qt Widgets and four Matplotlib Qt canvases, not HTML or a webview.
The supplied first two reference images informed whitespace, white rounded cards, quiet
sidebar and clear chart hierarchy. Remaining references informed restrained orange/blue/lime
accents. No reference brand, code, illustration or screenshot is distributed as app artwork.

## Navigation and progressive disclosure
- Exactly two semantic destinations: Chatbot and Dashboard Report. The native sidebar controls
  the two-page QTabWidget; settings is a separate masked-key dialog.
- Chatbot groups target, success definition and frozen benchmark into numbered cards.
  A persistent bottom action estimates calls across the complete five-repeat session.
- Advanced mappings, suite editor and actual optional AI rubric tools are collapsed initially.
  Deterministic intake is explicitly distinct from real paid judge/rubric requests.
- Dashboard: quality/repeat-count/latency/consistency KPIs, paired score trend and confidence
  interval, role breakdown, speed chart, duration chart, repeat table and case inspector.
  Case scores and baseline deltas are numeric, with exact expected/actual text and reasons.
- All metrics/provenance columns, prompt/evidence/rubric and raw trace/config are available
  on demand, not removed. Failed-case filtering and prompt/ID search aid review.

## Safety and truthfulness
Missing values remain N/A. Synthetic provenance is visible. High consistency does not imply
correctness; a fast wrong target can still regress. Native UI work changes no evaluator rule
semantics. No uploaded file executes automatically. Keys remain in memory/environment and
never enter suite/report/database exports. The worker/cancellation/shared budget path remains.

## Validation
Real Qt event-loop smoke and widget rendering are included under reports/v0.7. Offscreen
captures are diagnostic pictures of the actual app, not mockups; interactive desktop review
is separate. No browser or remote design service was used. The app is available on macOS, Windows and Linux via the Python source install. Interactive desktop review so far used Linux; packaged installers are not supplied yet. The Python launcher requires installed dependencies and a desktop session.

## Motion and percentage clarity (v0.4–v0.6)
Native QTimer/QPainter activity arc rotates only during real worker activity. QPropertyAnimation
smooths the progress bar up to already-confirmed completion counts; the text reports actual
completed repetitions. A single low-contrast QVariantAnimation fade follows terminal status.
No repetitive flashes. Reduced motion stops spinner/progress/fade animations while keeping
explicit running/cancelling/completed/error labels. Baseline and current scores are percentages;
statistical score-change intervals retain their percentage-point meaning in details/exports.

## Named testers and analysis (v0.6)
Heuristic testers reuse the eight evaluator roles and are labelled as local, not paid LLM
agents. A real optional planner uses the existing judge settings, consent, budget and
call ceilings, with pydantic validation of structured output. Findings in Find out problem
come only from completed observations. Generated Test Cases are persisted with the batch
and must be human-reviewed before becoming a new baseline.

## Opening logo
The native program opens with a short frameless splash of the Product Blinder wordmark
and a smaller “by NCCv3v” credit (`assets/branding/opening-logo.png`). Clicking the
splash, or waiting about two seconds, reveals the main window. Reduced motion shortens
the pause; `PB_SPLASH_MS=0` skips the delay. `--smoke` does not show the splash, so
automated checks stay fast.

## How Dull changed 指數 (v0.7)

index = 100 * max(0, S_old - S_now) / max(S_old, 1e-9)

S_old is the saved record mean quality and S_now is the current report mean quality, both in
[0, 1]. If either is missing, or suite_hash / batch_scoring_hash do not match, the UI shows
N/A and does not invent a number. Unchanged quality yields 0. Improved quality also yields 0
and is shown as Record → Current percentages, never as a dullness increase. Example:
S_old = 1.0, S_now = 0.40625 → 59.375, displayed as 59.4. This is “how much duller,” not a
causal explanation.
