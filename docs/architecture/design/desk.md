# Design — The teacher's desk (the landing page)

**Status:** agreed (Katlego, 2026-11-14 05:20) · **Owner:** Katlego (Claude) ·
**Related:** [console-ui.md](console-ui.md) (the screens), [conversation.md](conversation.md) §11 (the
colleague on every page)

## 1. What this covers

What a teacher sees after signing in. Until now the console opened straight on the "New assessment"
form, so it read as a tool for one job rather than a colleague's desk. The desk is the home page at
`/`. It has four parts, with the colleague box on the right as on every page:

| Part | Shows | Source |
| --- | --- | --- |
| **Mark a set** | The one primary action; opens the existing form at `/new` | — |
| **Your runs** | Each run, newest first: its title, script count, when it started and its state in the teacher's words ("Waiting for you", "Marking", "Ready for your approval", "Delivered", "Stopped"); a row opens the run | `GET /assessments` |
| **This term in the ATP** | The chosen subject's teaching weeks for a term, with consecutive weeks on one topic merged ("Weeks 1–4 · Algebraic expressions"), each with its DBE source | `GET /packs/{pack_id}/atp` |
| **The PoA this year** | Each formal task by term, with its weight and marks, and the circular it comes from | `GET /packs` |

No cost or money appears anywhere (Katlego, 2026-11-14).

Not covered: per-teacher ownership of runs (a single teacher pool for the event; every signed-in
teacher sees every run), and calendar dates for ATP weeks (the ATP data has none: its header says
dates differ by year).

## 2. Reference material

| Kind | Where |
| --- | --- |
| Visual reference | The layout Katlego chose on 2026-11-14 (below); the screen as built: `console-ui/20-desk.png` |
| Tokens | `apps/web/src/console.css` (the console's own) |
| Existing code | `screens/SetTaskScreen.tsx` (moves to `/new`, unchanged), `components/chrome.tsx` `TopBar` |

```
┌ Mothusi.  Good morning ─────────────────┬ Talk to Mothusi ──┐
│ [ Mark a set → ]                         │                   │
│ Your runs                                │                   │
│  Gr10 Maths T1 Test   waiting for you ●  │                   │
│  Gr10 Maths T1 Test   approved        ✓  │                   │
│ Term 1 in the ATP        PoA this year   │                   │
│  W1-4  Algebraic expr.   T1 Project 15%  │                   │
│  W5-6  Exponents         T1 Test    14%  │ [ Ask…      ] Send│
└──────────────────────────────────────────┴───────────────────┘
```

## 6. Contracts

```
GET /assessments
  200 [{assessment_id: str, title: str, state: AssessmentState, waiting: bool, halted: str | null,
        started_at: str (ISO), scripts: int, subject: str, grade: int}]     newest first; [] when none
  # read from each data/<id>/events.jsonl: the `created` event, the last `state` event, and whether a
  # needs_teacher question is open (waiting) or flags are queued; never the bundle (no names).

GET /packs/{pack_id}/atp?subject=<Mathematics|Accounting>
  200 [{week: int, term: int, term_week: int, topic: str, source: str}]     the pack's ATP rows
  404 {message}                                                          no ATP for that subject
  # term: weeks 1-11 → 1, 12-22 → 2, 23-33 → 3, 34-43 → 4 (the ATP file's header)
```

## 7. Structure

| Path | New? | Responsibility |
| --- | --- | --- |
| `services/core/src/mothusi_core/main.py` | change | the two GETs |
| `services/core/tests/test_desk.py` | new | §9 |
| `apps/web/src/screens/DeskScreen.tsx` | new | the desk |
| `apps/web/src/desk.css` | new | its layout |
| `apps/web/src/App.tsx` | change | `/` is the desk (was a redirect to `/new`) |

## 9. How this is verified

- pytest: `/assessments` lists a run created in the test with its state and `waiting`, newest first,
  and holds no learner name; `/packs/{id}/atp` returns all 43 rows with terms 1–4 and each row's
  source, and 404 for an unknown subject.
- Vitest: weeks merge into ranges; the runs list maps each state to its words and links to
  `/runs/:id`; the empty states read as invitations; no rand amount renders.
