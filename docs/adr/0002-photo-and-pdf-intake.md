# ADR 0002 — Photographed, scanned and PDF scripts on the golden path, read in Cape Town

**Status:** Accepted · **Date:** 2026-11-13 · **Decided by:** Katlego Tlhapiso · **Supersedes:** the scope document's
"Handwriting recognition on the golden path" out-of-scope line

## Context

On 2026-11-13 Katlego ruled that a tool someone must feed with typed scripts is useless to a teacher.
Mothusi adopts handwriting reading with a vision model on the golden path, alongside memo TOML and
scripts CSV intake.

## Decision

Scripts come in as phone photos, scans or PDFs. Typed text and CSV are still accepted. For each
script, the intake worker:

1. crops off the cover, where the name is, in Cape Town before any model sees the page;
2. reads a PDF's text layer directly (`pypdf`) when it has one;
3. sends the answer pages to the page reader (`qwen3-vl:8b-instruct` on the Cape Town G4dn, ADR 0001), which
   returns text per line with an uncertainty flag;
4. pseudonymises the text against the class list, exactly as for typed scripts.

The Marker never receives an image. A mark resting on an uncertain line is flagged for the teacher
beside the page crop (product user story US9). Tesseract is not used, because the vision model reads handwriting and
maths notation that OCR does not.

## Consequences

- `Script` and the intake contracts gain page images and per-line uncertainty, and the provider seam
  needs an image input. Both are planned in the task list before code.
- A synthetic photographed set is needed: the team writes answers by hand and photographs them, with no
  real learner's work. Evaluation reports the reader's accuracy on it.
- The cover crop assumes a fixed answer-sheet layout, so Mothusi supplies a printable answer sheet with
  the name box in a known place.
