# Design — rules engine

**Status:** agreed · **Owner:** the project researcher (Claude writes the design; Codex builds the implementation work) ·
**Spec:** the product requirements US3, US4

---

## 1. What this covers

The deterministic checks (the mentor's "level A") that run between agents: pure functions over the
contracts that never call a model and never change a mark. They are what a judge can read line by
line and what the Moderator's blind re-mark is compared against. This doc fixes each function's
signature, semantics and edge cases so that a bounded worker can build one without deciding
anything. It does not cover who calls them or when ([marking-pipeline.md](marking-pipeline.md) §4).

## 2. Reference material

| Kind | Where |
| --- | --- |
| The contracts these functions take and return | `packages/contracts/src/mothusi_contracts/models.py` |
| Pack data | `packs/dbe-caps/mathematics-gr10.toml` |
| PoA weights and the annexure's known copy errors | `preparation/docs/research/dbe-teachers-site.md` §1 |
| Existing code to match | `packages/rules/src/mothusi_rules/arithmetic.py` (style, `CheckResult` wording) |

## 3. Domain model

No new entities. Every function below takes and returns types from
[domain-model.md](domain-model.md). **Common rules for every check function:**

- Pure: no I/O except `load_pack`, no randomness except where a `seed` parameter is named.
- Never raises on bad marks; it returns `CheckResult(passed=False, detail=...)`. It raises
  `ValueError` only for inputs that cannot be checked at all (named per function).
- `CheckResult.check` is the module constant (e.g. `EVIDENCE`), `about` is the script id or memo
  id, and `detail` is a sentence a teacher can read, naming question ids.
- `Decimal` throughout; `ROUND_HALF_UP`; two decimals unless stated.

### `pack.py`

```
load_pack(path: Path) -> SubjectPack
    Reads TOML (stdlib tomllib) into SubjectPack via model_validate. ValueError (from pydantic)
    if the file does not fit the contract. Relative paths resolve from the current directory.

PACK_WEIGHTS = "pack_weights"
check_pack_weights(pack: SubjectPack) -> CheckResult
    passed when sum(t.weight_percent for t in pack.poa) == 100 exactly.
    detail on failure names the sum and lists each task as "name (term N): W%".
    about = f"{pack.pack_id}/{pack.subject}/gr{pack.grade}"
```

### `evidence.py`

```
EVIDENCE = "evidence"
check_evidence(marked: MarkedScript, script: Script) -> CheckResult
    For every Mark with awarded > 0: normalise(evidence_quote) must be a substring of
    normalise(answer.text) for the Answer with the same question_id.
    normalise = collapse every run of whitespace to one space, strip; case-sensitive;
    no other changes (a Maths quote must match symbols exactly).
    Failures (all collected, joined with "; ", sorted by question_id):
      "<qid>: no answer for this question"        when the script has no Answer with that id
      "<qid>: quote not found in the answer"       when the normalised quote is absent
    A Mark with awarded == 0 is never checked (nothing was earned).
    ValueError if marked.script_id != script.script_id (wrong pairing cannot be checked).
```

### `citations.py`

```
MEMO_LINE = "memo_line"
check_memo_lines(marked: MarkedScript, memo: Memo) -> CheckResult
    The memo half of grounding (check_evidence is the learner half). For every Mark with
    awarded > 0: normalise(memo_line) must be a non-empty substring of normalise(item.answer) or
    normalise(item.marking_notes) for the memo item with that question_id (normalise = collapse
    whitespace, strip; case-sensitive). Failures, sorted, joined with "; ":
      "<qid>: not in the memo"                     no memo item has that question_id
      "<qid>: memo line not found in the memo"     the citation is empty or not in answer/notes
    A Mark with awarded == 0 is never checked. Pass detail: "every mark above zero cites a line of
    the memo". about = marked.script_id. The Marker's loop runs it with check_script and
    check_evidence after every attempt.
```

### `poa.py`

```
find_task(pack: SubjectPack, name: str) -> PoATask
    Exact, case-sensitive match on PoATask.name; ValueError naming the available tasks otherwise.

convert_mark(script_id: str, raw: int, task: PoATask, pack: SubjectPack) -> ConvertedMark
    ValueError if raw < 0 or raw > task.max_marks.
    sba_points  = round_half_up(raw / task.max_marks * task.weight_percent, 2)
    year_points = round_half_up(sba_points * pack.sba_percent / 100, 2)
    out_of = task.max_marks
    Worked example (pack mathematics-gr10, task "Test", 50 marks, 14%, SBA 40%):
      raw 37 -> sba_points 10.36, year_points 4.14
      raw 50 -> sba_points 14.00, year_points 5.60
      raw 0  -> 0.00, 0.00
```

### `cognitive.py`

```
COGNITIVE_GRID = "cognitive_grid"
check_cognitive_grid(memo: Memo, pack: SubjectPack) -> CheckResult
    ValueError when pack.cognitive is None (the Planner logs "no cognitive-level grid in this
    pack; check skipped" and does not call).
    share(level) = sum(max_marks of items with that level) / memo.total_marks * 100, 2 dp.
    passed when every level in pack.cognitive.levels has |share - required| <= tolerance_points
    AND no item uses a level the pack does not define.
    detail lists every level as "<level>: <share>% (target <required>%)", with "OFF" appended to
    the ones outside tolerance, and "unknown level '<x>' on <qid>" for undefined levels.
    about = memo.assessment_id
```

### `moderation.py`

```
moderation_sample(marked: Sequence[MarkedScript], rule: ModerationRule, seed: int = 0) -> tuple[str, ...]
    n = min(len(marked), max(rule.min_scripts, ceil(len(marked) * rule.sample_percent / 100)))
    Order scripts by (total descending, script_id ascending) — deterministic.
    spread "top-middle-bottom": split the ordered list into three contiguous thirds (sizes as
      even as possible, extra scripts go to the earlier thirds) and take scripts round-robin
      from top, middle, bottom (first of each third, then second of each, ...) until n are taken.
    spread "random": random.Random(seed).sample over the script_ids in that same order, then
      sorted ascending (so the draw depends on the seed and the marks, not on input order).
    Returns script_ids in the order taken. Empty input -> empty tuple.

disagreements(first: MarkedScript, second: MarkedScript) -> tuple[Disagreement, ...]
    One Disagreement per question_id where awarded differs; ValueError if script_ids differ or
    the two do not mark the same set of question_ids. reason is "".

should_widen(disagreements: Sequence[Disagreement], questions_compared: int, rule: ModerationRule) -> bool
    True when len(disagreements) / questions_compared * 100 > rule.widen_above_gap_percent.
    ValueError if questions_compared <= 0.
```

### `items.py`

```
item_analysis(marked: Sequence[MarkedScript], memo: Memo) -> tuple[ItemStat, ...]
    One ItemStat per memo item, in memo order. scripts = how many MarkedScripts carry a Mark for
    that question. mean_awarded = mean of awarded, 2 dp. facility_percent = mean_awarded /
    max_marks * 100, 2 dp. flag = "re-teach" when facility_percent < 40,
    "review-item" when facility_percent > 95, else None.
    ValueError if marked is empty or a script carries a question_id the memo lacks, and when a
    memo question appears in no MarkedScript at all (ItemStat.scripts must be at least 1, so the
    statistic cannot be represented; the caller marks every script before analysing).
```

### `marksheet.py`

```
mark_sheet_csv(rows: Sequence[tuple[str, ConvertedMark]], task: PoATask) -> str
    rows pair a learner's display name (the teacher's side of the pseudonym map) with the mark.
    Columns, exactly: learner, raw, out_of, sba_points, year_points. Sorted by learner.
    A final row "TOTAL" with blank raw/out_of and the column sums, 2 dp. CRLF line endings
    (what spreadsheets expect), UTF-8, no BOM.
```

### `numeric.py`

```
equivalent(a: str, b: str) -> bool
    True when the two strings denote the same mathematical value or the same solution set.
    Parsing (sympy, `parse_expr` with implicit multiplication off):
      - a number or expression: "0.5", "1/2", "2x+1", "sqrt(2)"; equality = simplify(a - b) == 0
        for symbolic expressions, else |a - b| <= 1e-9 for numbers.
      - a solution set: "x=3 or x=-2", "x = 3; x = -2", "{-2, 3}", "x ∈ {3, -2}" -> the set of
        values; equal when the sets are equal (order irrelevant, values compared as above).
      - an equation: "y = 2x + 1" -> compared as the expression (lhs - rhs) against the other side's.
      - a system: "x = 2 and y = 3" (also "," or ";" between) -> {variable: value}; equal when the
        variables match and each value is equivalent.
      - an inequality: "x <= 2", "2 >= x", "x < 2" (also ≤ ≥) -> (direction, lhs - rhs) with > and >=
        read right to left so every inequality points the same way; equal when direction and
        residual agree ("x <= 2" vs "x < 2" is False).
      - an exponent may hold symbols, small constants and + - * / ("3^(n-1)", "2^(x+1)"); a nested
        power or a call inside an exponent is a ValueError, and every constant-only subtree of the
        exponent is folded and must stay within ±100 ("2^(100*100*100)" is refused).
      - "x = 2 and x = 3" (one variable, 'and') is a contradiction and a ValueError; "x = 3; x = -2"
        and "x = 3, x = -2" remain solution sets.
    Normalisation before parsing: strip, collapse whitespace, "×"->"*", "÷"->"/", "^"->"**",
    unicode minus -> "-", "√(" -> "sqrt(".
    ValueError (never a sympy exception) when either side cannot be parsed, naming the side.
    Bounds, because sympy evaluates eagerly and "9**9**9" would never return: at most 500
    characters and 200 syntax nodes per side; an exponent is a constant with |value| <= 100, a
    symbol, or a fraction of two constants, and anything nested ("2**(10**9)") is a ValueError.
    Never calls a model. Pure.
```

### `accounting.py`

```
foreign_entry_penalty(lines: Sequence[LedgerLine], cap: int) -> int
    Sum over lines with foreign == True of min(1, marks_scored) (one mark per foreign line, and
    only where a mark was scored on it), then min(sum, cap). ValueError if cap < 0.
    Worked example: three foreign lines, marks_scored 2, 0, 1, cap 2 -> penalty 2 (two lines
    scored, capped at 2); cap 5 -> 2; a fourth scored foreign line with cap 5 -> 3.

one_part_correct(expected: Decimal, parts: Sequence[Decimal], operation: Literal["sum", "difference"]) -> bool
    The "one part correct" rule: the operation is checked against `parts` and the learner's
    `expected` figure earns the mark when the operation over the parts reproduces it (within
    0.005) OR at least one part is itself correct and the operation is applied correctly to the
    learner's own parts. Concretely, with the memo's correct parts `parts` and the learner's final
    figure `expected`: True when expected == sum(parts) (or parts[0] - sum(parts[1:]) for
    "difference"); else False. ValueError if parts is empty.
    (The second branch, "the learner's own parts", needs the learner's figures and is the
    Marker's job via the memo note; this function decides the first branch deterministically.)
```

### `report.py`

```
moderation_report_md(assessment_title: str, sample: Sequence[str],
                     disagreements: Sequence[Disagreement], resolutions: Mapping[str, str],
                     questions_compared: int, moderator: str) -> str
    Markdown with exactly these sections, in order:
      "# Moderation report: <assessment_title>"
      "## Sample" — a table (script, included) listing every script id in `sample`, in order.
      "## Disagreements" — a table (script, question, first mark, second mark, gap, resolution);
         one row per Disagreement, resolution = resolutions[f"{script_id}/{question_id}"] or
         "unresolved"; when there are none, the line "No disagreements in the sample."
      "## Agreement" — "Agreement rate on the sample: <rate>% (<n> of <m> questions)." where
         m = questions_compared, n = m - len(disagreements), rate = n / m * 100 to 2 dp
         (ROUND_HALF_UP). ValueError if questions_compared < len(disagreements) or <= 0.
      "## Signatures" — three lines: "Marker: ____________", "Moderator (<moderator>): ____________",
         "Head of Department: ____________  Date: ____________".
    LF line endings; no trailing spaces. Pure.

class_feedback_md(item_stats: Sequence[ItemStat], top_n: int = 3) -> str
    "# Class feedback" then "## Weakest questions" with a table (question, facility %, flag) of
    the `top_n` lowest facility_percent items (ties by question_id), then "## Strongest" with the
    top_n highest. ValueError if item_stats is empty or top_n < 1.
```

### `catchup.py`

```
load_atp_weeks(path: Path) -> tuple[AtpWeek, ...]
    TOML: [[week]] tables with week, topic, source. Sorted by week ascending. ValueError if two
    rows share a week or the file does not fit AtpWeek.

CATCH_UP = "catch_up_weeks"
check_catch_up(plan: Sequence[CatchUpItem], remaining: Sequence[AtpWeek]) -> CheckResult
    passed when every CatchUpItem.week is a week in `remaining` AND its atp_source equals that
    week's source. detail lists each failing item as "<topic>: week <w> is not in the remaining
    ATP" or "<topic>: source does not match week <w>"; on pass, "<n> items placed in <k> remaining
    weeks". about = "catch-up plan". Empty plan passes with "0 items placed in <k> remaining weeks".
```

### `notes.py`

```
NOTE_GROUNDING = "note_grounding"
check_note_grounding(note: LearnerNote, script: Script, marked: MarkedScript) -> CheckResult
    The deterministic half of the Reviewer (the agent contracts `reviewer`): what code can decide, code
    decides. ValueError if script.script_id != marked.script_id or
    note.pseudonym != script.learner_pseudonym.
    Quotes: every span inside straight or curly double quotes, guillemets or backticks in
    note.mark_with_quote ("...", “...”, «...», `...`) must appear, whitespace-normalised and case-sensitive (as check_evidence),
    in some Answer.text of the script. Failure: 'quote not found in the script: "<span>"'.
    No quote at all: 'no quote from the learner's work'.
    Marks: every "<n>/<m>", "<n> / <m>" or "<n> out of <m>" in mark_with_quote (integers) must equal
    some Mark's awarded/max_marks or marked.total/sum(max_marks). Failure: 'mark <n>/<m> is not in
    the marked script'. Every "<n>%" must equal round(awarded/max*100) for one of those pairs.
    Failure: 'mark <n>% is not in the marked script'.
    Labels: these patterns, case-insensitive, with any run of whitespace between words and a
    straight or curly apostrophe in can't, anywhere in outcome, mark_with_quote or next_task,
    each a failure "labels the learner: '<matched text>'":
      \b(is|are|was|were) (weak|poor|slow|lazy|bad|hopeless|careless)\b
      \b(better|worse|slower|faster) than\b
      \b(top|bottom) of the class\b
      \bnot (clever|smart|bright|good at)\b
      \b(can't|cannot|can not) do\b
    Praise without evidence: "well done", "excellent", "brilliant" (case-insensitive) in
    mark_with_quote when it holds no quote span: failure 'praise without a quote'.
    Failures collected, sorted, joined with "; ". about = note.pseudonym.
```

### `blind.py`

```
BLIND = "blind_brief"
check_brief_blind(brief: Brief, names: Collection[str]) -> CheckResult
    The guard the Planner runs before dispatching a Brief (the product requirements US2 scenario 1; system criterion
    "no learner name in any Brief to the Marker or Moderator").
    Walk brief.inputs recursively (dict values and keys; list, tuple, set and frozenset items;
    contract model fields; bytes decoded as UTF-8; and a str that is itself JSON, decoded and
    walked as data); for every str reached, compare after normalising:
    unicodedata.normalize("NFKC", s) (fullwidth and ligature forms fold), drop every character of
    category Cf (zero-width joiners and spaces, soft hyphens), collapse runs of whitespace to one
    space, strip, casefold(). Normalise each name the same way. Never serialise to JSON for this (json.dumps escapes non-ASCII and tabs, so
    "Zoë M" would not be found).
    Names: for every agent except writer and executor, a normalised name of 3+ characters that is
    a substring of any normalised string in the inputs is a failure
    "learner name '<name>' appears in the brief" (the name's original spelling in the message).
    First marks: for agent == moderator, any dict anywhere in inputs (the same recursive walk)
    whose keys include all of {"marks", "total", "marker"} (a MarkedScript shape), or any key
    named "marked", is a failure "moderator brief carries first marks (<count> objects)".
    Pass detail: "brief is blind". about = brief.brief_id. Failures sorted and joined with "; ".
    Pure; never raises for a well-formed Brief.
```

## 4. Flow

Called by the Planner at the points drawn in [marking-pipeline.md](marking-pipeline.md) §4.
No internal flow: each function is one step.

## 5. State

None.

## 6. Contracts

The signatures in §3 are the contract, verbatim. `mothusi_rules/__init__.py` re-exports every public
name listed there.

## 7. Structure

| Path | New? | Responsibility |
| --- | --- | --- |
| `packages/rules/src/mothusi_rules/arithmetic.py` | exists | `check_totals`, `check_bounds`, `check_script` |
| `packages/rules/src/mothusi_rules/pack.py` | exists | `load_pack`, `check_pack_weights` |
| `packages/rules/src/mothusi_rules/evidence.py` | exists | `check_evidence` |
| `packages/rules/src/mothusi_rules/citations.py` | exists | `check_memo_lines`, `MEMO_LINE` |
| `packages/rules/src/mothusi_rules/poa.py` | exists | `find_task`, `convert_mark` |
| `packages/rules/src/mothusi_rules/cognitive.py` | exists | `check_cognitive_grid` |
| `packages/rules/src/mothusi_rules/moderation.py` | exists | `moderation_sample`, `disagreements`, `should_widen` |
| `packages/rules/src/mothusi_rules/items.py` | exists | `item_analysis` |
| `packages/rules/src/mothusi_rules/marksheet.py` | exists | `mark_sheet_csv` |
| `packages/rules/src/mothusi_rules/numeric.py` | exists | `equivalent` |
| `packages/rules/src/mothusi_rules/accounting.py` | exists | `foreign_entry_penalty`, `one_part_correct` |
| `packages/rules/src/mothusi_rules/report.py` | exists | `moderation_report_md`, `class_feedback_md` |
| `packages/rules/src/mothusi_rules/catchup.py` | exists | `load_atp_weeks`, `check_catch_up`, `CATCH_UP` |
| `packages/rules/src/mothusi_rules/notes.py` | new | `check_note_grounding`, `NOTE_GROUNDING` |
| `packages/rules/src/mothusi_rules/blind.py` | new | `check_brief_blind`, `BLIND` |
| `packages/rules/tests/test_<module>.py` | new, one per module | the cases in §9 |

## 8. Decisions & alternatives

| Decision | Chosen | Rejected, and why |
| --- | --- | --- |
| Evidence matching | Whitespace-normalised, case-sensitive substring | Fuzzy matching: a quote that "nearly" appears is exactly the fabricated evidence the check exists to catch |
| Rounding | `ROUND_HALF_UP`, 2 dp, once per step | Python's default banker's rounding: it surprises teachers who check by hand |
| Sample order | Deterministic by total then id | Random by default: a moderator's sample must be reproducible for the record |
| Re-teach threshold | Facility below 40% | A pack parameter: no DBE source fixes one; revisit if a province's instruction does |
| Numeric equivalence | sympy, exact for symbols, 1e-9 for numbers | String comparison: "1/2" and "0.5" are the same answer; a model's judgement: the whole point is that code decides |
| Reports | Deterministic markdown renderers for the moderation report and class sheet | Letting the Writer draft them: the numbers in them must be the computed ones, never re-typed by a model |

Deviations from architecture-defaults.md: none.

## 9. How this is verified

Each module's test file covers, at least: the worked example or happy path; each failure sentence
in §3 produced for the right input; the `ValueError` cases; and one boundary (exactly on
tolerance, exactly 40%, n equal to the class size). `uv run --frozen pytest packages/rules/tests`
is the Verify for every rules task. Pyright strict must stay at zero errors.

## 10. Open questions

- [ ] `equivalent` compares equations by the residual `lhs - rhs`, so `2x+1 = y` and `y = 2x+1`
      compare False although they are the same equation. Acceptable for the Setter (memos state the
      subject on the left); revisit if a pack's memos do not. Likewise a single `x=3` against the
      set `{3}` is False (scalar vs set); the Setter should state solution sets as sets on both sides.
- [ ] The moderation sample rule (10%, minimum 6, top-middle-bottom) is Mothusi's working rule until
      the province's SBA moderation instruction is confirmed; the pack carries it as data so only
      the TOML changes.
