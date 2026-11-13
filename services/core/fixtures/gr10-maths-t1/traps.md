# Grade 10 Maths Term 1 class set: what is planted, and where

A synthetic class of 32 learners for the demo (the demo's golden path) and the tests. Nothing here is a real learner, school or DBE paper. Names in `scripts.csv` are
invented; intake replaces them with `L-001` … `L-032` in class-list order before any agent reads a
script.

| File | What it is |
| --- | --- |
| `memo.toml` | 9 questions, 50 marks, each with its question, answer, marking notes and cognitive level |
| `scripts.csv` | 278 answers from 32 learners (a learner with no row for a question did not attempt it) |
| `atp-weeks.toml` | ATP weeks 10–17, the weeks still to come after the test; catch-up work goes only here |

## The four planted traps

| Trap | Where | What a correct run does | Caught by |
| --- | --- | --- | --- |
| Memo error | `memo.toml` item **2.3**: memo says `x = -2`; 2^(x+1) = 1/8 = 2^(−3) gives **x = −4** | The Setter's answer differs; the run stops at `memo_check` and asks the teacher before any marking | Setter, `equivalent` |
| Fabricated evidence | **L-014**, question **3.1**: the working is correct but scattered; the tidy line `x = 2 and y = 3` appears nowhere in it | A quote the Marker tidies up fails `check_evidence`; attempt 2 quotes the learner's own words | `check_evidence`, Marker retry |
| Over-mark | **L-021**, question **4**: `w + 2w + 3 = 48` (perimeter not doubled), so `w = 15`, `l = 33` | Memo credit is `l = 2w + 3` (1) and units (1): **2/6**. A Marker that follows the wrong equation through gives about 5/6; the blind Moderator gives 2/6 and the script is re-marked | Moderator, `disagreements` |
| Ungrounded note | **L-007**: full marks on every algebra question (1.x, 3.x, 4), wrong on all of exponents (2.x) | A note saying L-007 "is weak at algebra" is rejected; the rewrite points at exponents and quotes 2.1 or 2.2 | Reviewer |

L-014, L-021 and L-007 are pseudonyms: they hold only while the CSV keeps its class-list order
(Neo Cele is the 14th learner, Sipho Qwabe the 21st, Katlego Radebe the 7th).

## Where each question comes from

All from the Grade 10 Mathematics ATP (`preparation/RATP Mathematics Grade 10 - 2024.pdf`); the test
is ATP week 9.

| Item | Topic | ATP week | Level | Marks |
| --- | --- | --- | --- | --- |
| 1.1 | Quadratic equation by factorisation | 7 | routine procedures | 5 |
| 1.2 | Products of algebraic expressions | 1–3 | knowledge | 6 |
| 1.3 | Multiplying algebraic fractions | 4 | routine procedures | 5 |
| 2.1 | Laws of exponents | 5 | knowledge | 6 |
| 2.2 | Simplifying with prime bases | 5–6 | complex procedures | 6 |
| 2.3 | Exponential equation | 6 | routine procedures | 4 |
| 3.1 | Simultaneous linear equations | 7 | complex procedures | 8 |
| 3.2 | Linear inequality and number line | 7 | routine procedures | 4 |
| 4 | Word problem to a linear equation | 8 | problem solving | 6 |

Levels add up to 12 / 18 / 14 / 6 marks = 24 / 36 / 28 / 12 %, inside the pack's 5-point tolerance
of CAPS's 20 / 35 / 30 / 15, so `check_cognitive_grid` passes.

## Regenerating

`scripts.csv` came from a seeded generator (seed 20261108): answers drawn from a bank of full,
partial and wrong working per question by each learner's ability, with the traps above written in by
hand. Edit the CSV directly if a trap needs to change, and update this file in the same commit.
