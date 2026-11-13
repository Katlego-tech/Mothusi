# Design — the event log

**Status:** agreed · **Owner:** the project researcher (Claude writes; Codex builds the implementation) ·
**Spec:** the product requirements US8, US2 scenario 1

---

## 1. What this covers

The record of what the agents did: one `Event` per Brief, Result, check, decision, state change and
teacher action, appended to a JSONL file per assessment, read back for the console's live log and
for the moderation record. It fixes the record type, the store and the one-line rendering. It does
not cover the SSE endpoint or the graph nodes that emit events or the console.

## 2. Reference material

| Kind | Where |
| --- | --- |
| What must be visible and why | [marking-pipeline.md](marking-pipeline.md) §4 "What makes the self-correction real", §9 |
| The judges' point 1 and 2 | the judging criteria |
| Existing code to match | `packages/contracts/src/mothusi_contracts/events.py`, `packages/events/src/mothusi_events/render.py` |

## 3. Domain model

`Event(seq, at, assessment_id, kind, agent, summary, payload)` with `EventKind` in
`created | state | brief | result | check | decision | disagreement | needs_teacher | usage |
delivered | teacher` (the console's ten kinds, console-ui.md §6, plus `teacher` for each answer the
teacher gives; the Planner implementation widened the enum from the first six). Each kind's `payload` is the shape in
console-ui.md §6. `at` is timezone-aware; `summary` is one line
of at most 200 characters; `payload` is JSON (the Brief, the Result, the CheckResult, or the
decision's inputs, as `model_dump(mode="json")`). **No learner name is ever in a summary or
payload** (pseudonyms only); the Writer's outputs, which carry names, are files in the bundle, not
events.

## 4. Flow

The Planner calls `log.append(...)` at each point drawn in the pipeline sequence; the API's SSE
endpoint tails the same file; after the run, `render(log.read())` is the plain-text record.

**Failure paths:** an append with the wrong `seq` is refused (the log is the authority on order); a
corrupt line stops `read()` with the line number, never a partial silent read.

## 5. State

None. (The assessment's state machine is in [marking-pipeline.md](marking-pipeline.md) §5; a
`state` event records each transition.)

## 6. Contracts

```
summarise(event: Event) -> str                                                       (exists)
    f"{seq:05d} {at.isoformat(timespec='seconds')} {kind:<8} {agent or '-':<9} {summary}"
render(events: Iterable[Event]) -> str                                               (exists)

EventLog(path: Path)
    JSONL, one Event per line (model_dump_json), UTF-8, LF. The file is created on first append.
    next_seq() -> int                  len(existing events); 0 for a new log
    append(event: Event) -> None       ValueError f"expected seq {n}, got {event.seq}" unless
                                       event.seq == next_seq(); ValueError if event.assessment_id
                                       differs from the log's (the first event sets it)
    read() -> tuple[Event, ...]        every event in order; ValueError f"line {n}: <reason>" on a
                                       line that is not a valid Event
    tail(n: int) -> tuple[Event, ...]  the last n (n >= 1 else ValueError)
    assessment_id -> str | None        None for an empty log
```

## 7. Structure

| Path | New? | Responsibility |
| --- | --- | --- |
| `packages/contracts/src/mothusi_contracts/events.py` | exists | `Event`, `EventKind` |
| `packages/events/src/mothusi_events/render.py` | exists | `summarise`, `render` |
| `packages/events/src/mothusi_events/log.py` | new | `EventLog` |
| `services/core/src/mothusi_core/events.py` | later | emits events from the graph; SSE |

## 8. Decisions & alternatives

| Decision | Chosen | Rejected, and why |
| --- | --- | --- |
| Store | JSONL file per assessment | Postgres table: the checkpointer already has Postgres, but a file the judges can `cat` is the transparency the rubric asks for; the API can mirror it later |
| Order | The log assigns `seq` and refuses gaps | Timestamps: two events in one millisecond have no order |
| Names | Never in events | Convenient display: the log is shown live to judges and must be safe to show |

## 9. How this is verified

- Append three events, read them back equal; `seq` 5 after 3 is refused with the sentence; a
  second assessment_id is refused; a corrupt line names its number; `tail(2)` is the last two.

## 10. Open questions

- [ ] Rotation or size limits: a 32-script run is a few hundred events; nothing needed before the event.
