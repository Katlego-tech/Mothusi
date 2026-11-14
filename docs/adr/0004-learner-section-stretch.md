# ADR 0004 — Learner section (stretch): practice tests from the ATP

**Status:** Accepted as stretch · **Date:** 2026-11-13 · **Decided by:** Katlego Tlhapiso · **Builds
on:** the scope document’s "Stretch" section (2026-11-13)

## Decision

- **Learner sign-in.** A pseudonym code and PIN handed out by the teacher. Cognito holds no name,
  email or phone number. It needs parental consent first (POPIA s35(1)(a)).
- **Practice tests.** The Setter sets a test and memo from the pack's ATP weeks and CAPS sources. Code
  checks it, the Setter sits it, and the teacher approves it once. Attempts are marked by the same
  Marker and rules against that approved memo.

## Open

- [x] **Non-negotiable I — decided by Katlego, 2026-11-13:** "practice tests may show results as their
  purpose is to help the learner improve". The teacher's one approval of a practice test and its memo
  covers every attempt's result. Learners see only the code-checked mark, the evidence quote and the
  memo's worked answer, never free model prose. Recorded in implementation non-negotiable 1, the product
  non-goals and the contributor grounding rules. Nothing else reaching a learner is affected.
- **Scope.** The scope document rules out "a lesson-plan generator". The learner section's design doc must draw
  the line.
