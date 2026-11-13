# TALIS 2024: what the full report adds to Mothusi

Second pass over the DBE's *TALIS 2024 South Africa Country Report* (226 pages, saved as
`TALIS 2024 Country Report.pdf`), read in full on 2026-11-04 by the project researcher (via Claude Code, second session).
The first pass (high-value-targets.md §2.1) took the time-use tables, the stress
headline, AI use and learning needs. This note records what that pass missed, and corrects two numbers the
report's own prose gets wrong. Page numbers are the printed ones (PDF page = printed page + 1).

**Caveat that applies to every number:** TALIS surveys Grade 8–9 teachers (ISCED 2), self-reporting on their
most recent complete week. There is no FET-specific measure. Treat the figures as the floor for Grades 10–12,
where exams weigh more, and get the FET figure from the teacher interviews.

---

## 1. Two corrections, checked against the rendered figure pages

### 1.1 Marking is the single most intense stressor, not the second

Figure 82 (p. 150, "Sources of Teachers' stress at schools"), the **"A lot"** column:

| Stressor | "A lot" | "Quite a bit" + "A lot" |
|---|---|---|
| **Having too much marking** | **49%** | **72%** |
| Being held responsible for students' achievement | 44% | 70% |
| Having too much administrative work (e.g. filling out forms) | 38% | 66% |
| Maintaining classroom discipline | 32% | 57% |
| Having too many lessons to teach | 30% | 58% |
| Having too much lesson preparation | 28% | 56% |
| Keeping up with changing requirements from authorities | 27% | 55% |

The prose (para 24, p. 150, repeated in the executive summary para 56) says "excessive lesson preparation (49%)
and too much marking (44%)". Those are the marking and achievement rows, misread. The figure agrees with the
executive summary's own Figure 10 (marking 72, achievement 70, admin 66), so **use the figure, not the prose**.

What changes for us: high-value-targets.md §3 says lesson plans "have the top
stressor (49%)". They don't: lesson preparation is 28% "a lot". Marking's lead over every other target widens.
The pitch line becomes: *marking is the task South African teachers most often call a heavy stressor (49%
"a lot"), the task with the largest excess over the OECD (7.1 h a week against 4.4), and the one they use AI
for least (50% of AI users).*

### 1.2 The barriers to AI are infrastructure and skills, not school bans

Figure 73 (p. 136, "Reasons why teachers do or don't use AI to facilitate learning"; red = No, green = Yes),
among teachers who did not use AI:

| Reason | Yes |
|---|---|
| School lacks the infrastructure | 75% |
| Do not have the knowledge and skills | 57% |
| Feel overwhelmed | 38% |
| Do not believe we should use AI | 28% |
| School does not allow | 16% |

Para 153 reads the "No" bars as "Yes" ("School does not allow (84%)"). Para 157 and executive-summary para 41
have it right. What changes for us: Mothusi has to work where infrastructure is thin (a phone, a photo, a
WhatsApp message, low bandwidth, the Ollama fallback), and has to be simple enough not to overwhelm (one goal
typed in one sentence, no prompt-writing). A school ban is the smallest barrier. No DBE guideline on AI in
schools was found on 2026-11-04; the closest official text is the draft national AI policy Cabinet approved on
25 March 2026, so Mothusi's own rules (approval gate, audit log, synthetic data, pseudonymised scripts) are the
governance story for now.

---

## 2. Findings the first pass didn't use, ranked by what they change

1. **Fixed mindset: South Africa is the global outlier** (Table 2 p. 12; paras 30–32; Recommendation 3
   p. 220). 21.2% of teachers *strongly* agree that everyone has a fixed amount of intelligence (OECD 2.8%),
   17.1% that intelligence can't change (OECD 2.3%), and 50.1% agree or strongly agree. The DBE's third
   recommendation is a national programme to shift teachers to a growth mindset.
   **Addition:** the Feedback Writer writes growth-framed, evidence-based notes (the step that earned the mark,
   the exact error, the next action), never a label. The Reviewer gets a check, in code and model, that rejects
   labelling ("weak", "careless", "not a maths person"), comparison with other learners, and praise with no
   evidence. The same rule applies to the item analysis (gaps are teachable content, never "a weak class") and
   the HOD bundle. Plant one such comment in the demo set so the Reviewer is seen bouncing it. Cheap, visible,
   and it is the DBE's own recommendation.

2. **Feedback structure: the report's own model is feed-up, feedback, feed-forward** (paras 43–44 p. 102,
   citing Hattie and Timperley). Figure 53 (p. 103): 48% of teachers always give oral or written feedback, but
   only 22% always ask learners to assess their own progress and 7% never do. Recommendation 1 names
   "Assessment for Learning implementation" as a gap in teacher education.
   **Addition:** every learner note has three fixed parts enforced by the output schema, not the prompt:
   *where you're going* (the CAPS/ATP outcome the question tested), *where you are* (the mark and a quote of the
   learner's own work), *how to close the gap* (one task from the catch-up plan). Then one self-assessment line
   ("tick what you can now do before the re-test").

3. **The DBE tells schools to cut written feedback** (Recommendation 6, p. 221: SMTs should "move from
   extensive marking and written feedback methods (like verbal or whole-class feedback)"), because it costs
   teacher time. Recommendation 9 (p. 221) says the department should "formalise the integration of AI tools for
   streamlining the process of marking and corrections (7 hours/week, highest internationally)".
   **Addition:** a **whole-class feedback sheet**: the top three errors with a worked correction for the next
   lesson, and the re-teach slots in the remaining ATP weeks. The teacher gets individual notes *and* the
   whole-class method the DBE recommends, without the time cost. Put Recommendation 9 on a slide, quoted.

4. **Language of learning and teaching (LOLT)** (paras 34–36 p. 13; Recommendation 11 p. 221). 65% of
   teachers work in schools where more than 10% of learners' first language is not the LOLT (OECD 25%). More
   than half say over 10% of their learners struggle to understand the LOLT. 24% say *all* their learners speak
   a different first language. North West is worst.
   **Additions:** (a) the Marker separates "misread the question" from "doesn't know the content" and says
   which, with the quote; the item analysis carries a **language-load flag** on questions where errors cluster at
   the reading step. (b) The pre-moderator checks stem readability and sentence length against the exam
   guideline. (c) Learner notes in the home language stay on the roadmap, now backed by the Eastern Cape
   versioning prep papers in isiXhosa and Sesotho (dbe-teachers-site.md §2.13).

5. **A young, under-mentored workforce.** 23% of teachers are under 30 (OECD 10%); over 30% have five or
   fewer years' experience; 37% came through a fast-track programme (paras 9, 11, 15). 42% of teachers have no
   assigned mentor (para 82 p. 66); only 49% of novices do (Figure 3). The DBE's induction programme started in
   2024 (para 110). Only 28% of principals often give observation-based feedback (para 49).
   **Addition:** an **explain mode**: every mark decision carries the memo line and the marking principle it
   applied (consistent-accuracy marking, method vs accuracy marks, "one part correct"), so a novice learns the
   convention while checking the work. The moderation report doubles as feedback to the teacher on their own
   task: cognitive-level spread, memo errors, questions nobody could answer.

6. **Accountability pressure.** 44% "a lot" stressed by being held responsible for learners' achievement,
   27% by changing requirements from authorities (Figure 82).
   **Addition:** Mothusi's outputs are the evidence file that answers the accountability question (teacher-file
   bundle, moderation report, intervention record), and Mothusi tracks which circular is current
   (dbe-teachers-site.md §1) so the teacher doesn't have to.

7. **Special educational needs.** 73% cite modifying lessons for learners with SEN as a stressor (para 24);
   59% had no SEN training in the last year (Recommendation 2); it is the top professional-learning need (44%);
   52% of AI users already use AI for it. Already in the plan (differentiated catch-up tasks, SNA 1 draft,
   concessions prompts); this is the evidence.

8. **Teachers set their own assessments.** 47% report full autonomy and 39% substantial autonomy in choosing
   assessment activities (para 67, p. 112). Task setting (target 3) is real teacher work, not only the
   province's.

9. **Common standards are how SA teachers already collaborate.** Working with other teachers "to ensure
   common standards in evaluations for assessing student progress" is the most frequent form of collaboration
   (57%, para 51 p. 16). Moderation is that practice; Mothusi's Moderator joins it, and the HOD view across
   classes is the natural second user.

10. **Where the hours are, by province** (Table 7, p. 52; marking hours a week): Free State 9.3, Mpumalanga
    8.3, Limpopo 7.4, Northern Cape 7.4, Gauteng 7.1, KwaZulu-Natal 6.7, Eastern Cape 6.6, North West 6.5,
    Western Cape 6.3. Total job-related time averages 38 hours (para 25). Only 66.4% of class time is spent
    teaching, 14.9% on administration and 18.3% on keeping order (para 58). Useful for choosing a pilot province
    on the "what we'd build next" slide.

11. **Overwhelm.** 38% of non-users feel overwhelmed by AI (Figure 73). One goal in, finished work out; no
    settings, no prompts, no model names on the teacher's screen.

12. **Assistants reduced workload, and the DBE wants more of them.** Recommendation 7 (p. 221): expand the
    PYEI education-assistant programme and fund administrative support staff. This is the second official
    basis, after the REAP report, for the line in curriculum-assistant-role.md
    §6: Mothusi keeps that support going where the assistants have gone.

---

## 3. Numbers for slides (checked against the figure or table page)

| Claim | Number | Where |
|---|---|---|
| Marking hours a week, SA vs OECD | 7.1 vs 4.4 (highest of the compared countries; Singapore next at 6.4) | Table 6, p. 51 |
| Marking as a heavy stressor | 49% "a lot", 72% "quite a bit" or "a lot" | Figure 82, p. 150 |
| AI users who use it to mark | 50% (the least common use) | Figure 72, p. 136 |
| Teachers who have used AI | 48% (OECD 36%) | Figure 6, p. 13 |
| Fixed mindset | 50.1% agree intelligence can't change; 21.2% strongly agree it is fixed (OECD 2.8%) | Table 2, p. 12; para 30 |
| Learners' first language ≠ LOLT in >10% of the class | 65% of teachers (OECD 25%) | Figure 6, p. 13 |
| No assigned mentor | 42% of teachers | para 82, p. 66 |
| Self-assessment by learners | only 22% of teachers "always" ask for it | Figure 53, p. 103 |
| DBE recommendation on AI and marking | "formalise the integration of AI tools for streamlining the process of marking and corrections" | Rec. 9, p. 221 |

---

## 4. What to do with the first-pass note

high-value-targets.md §2.1 carries the prose stress numbers (49% lesson preparation,
44% marking) with a ⚠. The figure settles it (§1.1 above): swap the numbers and drop the "lesson plans have the
top stressor" argument in §3; the ranking doesn't change, the gap grows.
