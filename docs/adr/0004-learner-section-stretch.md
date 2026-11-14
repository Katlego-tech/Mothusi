# ADR 0004 — Learner section (stretch): practice tests from the ATP, with guided solving

**Status:** Accepted as stretch · **Date:** 2026-11-13 · **Decided by:** Katlego Tlhapiso · **Builds
on:** the scope document’s "Stretch" section (2026-11-13)

## Decision

- **Learner sign-in.** A pseudonym code and PIN handed out by the teacher. Cognito holds no name,
  email or phone number. It needs parental consent first (POPIA s35(1)(a)).
- **Practice tests.** The Setter sets a test and memo from the pack's ATP weeks and CAPS sources. Code
  checks it, the Setter sits it, and the teacher approves it once. Attempts are marked by the same
  Marker and rules against that approved memo.

- **Guided test-taking and problem solving (Katlego, 2026-11-14).** Practice works like pair
  programming, with the learner driving and Mothusi navigating:
  - The learner writes each step of their working.
  - Code checks each step as it is written, with the same equivalence checks the Marker's rules use.
  - At the first step that goes wrong, Mothusi points to that step and offers a hint. It never
    gives the answer before the attempt is submitted.
  - The learner can ask for the next hint, or ask "why is this step wrong?".
- **Hints tailored by the teacher's feedback.** The guidance for each learner starts from what their
  teacher approved for them:
  - the "next" task in their feedback note;
  - the misconception the item analysis found on their script;
  - their group in the catch-up plan.

  A learner whose note says "set up the equation from the words first" is first steered to that
  step on a new problem.
- **What the learner reads stays inside Non-negotiable I.** Every hint comes from a ladder of hints
  written with the practice test's memo and approved by the teacher in the same single approval. The
  model only *chooses* which approved hint fits the learner's step and feedback. Code checks the steps.
  The learner never reads free model prose.

## Open

- [x] **Non-negotiable I — decided by Katlego, 2026-11-13:** "practice tests may show results as their
  purpose is to help the learner improve". The teacher's one approval of a practice test and its memo
  covers every attempt's result. Learners see only the code-checked mark, the evidence quote and the
  memo's worked answer, never free model prose. Recorded in implementation non-negotiable 1, the product
  non-goals and the contributor grounding rules. Nothing else reaching a learner is affected.
- **Scope.** The scope document rules out "a lesson-plan generator". The learner section's design doc must draw
  the line.
- **Free-text hints.** Hints written by the model on the spot would read more naturally than an
  approved ladder, but they would put model prose in front of a learner. Non-negotiable I and the
  decision above rule that out. Katlego to decide whether to allow it, and under what check.
