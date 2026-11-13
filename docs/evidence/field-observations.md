# Field observations: the project researcher's time as a BEEI curriculum assistant

First-hand evidence, recorded 2026-11-04 from the project researcher's account. It sits beside the survey numbers
(../idea.md, "How we treat evidence"). Learners, teachers and the school are not named. Open
questions are in §5; answers will be added under each observation.

---

## 1. What the project researcher observed

### O1. A public school, and a chain of submissions

- Placed in a **public school** under the Basic Education Employment Initiative (BEEI).
- Teachers have many documents to submit to their **HODs**. HODs have their own submissions. The
  **principal** submits documents to the department.
- **In his first week** he was asked to **file the principal's documents for the district** (the principal
  compiled them; see Q3). They held budgets and "some reports I couldn't make sense of".

### O2. The official role vs how the school deployed it

- **DBE training matched the published role.** By the book, assistants are assigned to subjects and do their
  duties in those subjects. Other schools did that.
- **His school didn't assign subjects.** Assistants were **deployed wherever they were needed**, to stop teachers
  "capturing" an assistant to serve one subject in one grade. **The department allowed this.**
- the project researcher's reading: "the first indicator of a system under stress, and when I realised automation would be my
  best friend."

### O3. Student teachers didn't remove the load

- The school periodically took in a **cohort of student teachers**, who stood in for teachers who had too much
  work or couldn't come in.
- That **didn't free the assistants from their duties**. Even with student teachers, there was still work to do.

### O4. The work actually done, by subject

| Subject | What he did | Against the official role (curriculum-assistant-role.md §2.1) |
|---|---|---|
| **Mathematics** | **Taught for a full week**, using content curated by the teacher | The manuals say assistants are "not required to teach" |
| **Physical Sciences** | **Marked class exercises for 3 days** | Matches the manual (informal marking) |
| (colleagues) | **Marked informal tests.** These colleagues were taking a one-year SACE course | Matches the manual (informal marking). Formal tests and exams were marked by the teachers (Q8) |
| **Information Technology** | Was in class with the teacher and **helped struggling learners**, simplifying concepts and tailoring explanations to each learner as the conversation went | Matches the manual (support during lessons) |

### O5. Automation 1: mark conversions, on a phone

- Part of the admin work is **calculations (conversions)**. **One teacher** had to do them for **about three
  classes** while doing other tasks.
- It came when the **student teachers had left and some teachers were absent**: the school was **understaffed**, and
  the conversions had to be done faster.
- Colleagues proposed **more people (3 or 4)**.
- the project researcher **wrote a Python script on his phone (a `while` loop) and ran it in Termux**. That cut the task to
  **2 people**: one reads the raw figures and writes the results down, the other types the figures into the
  converter and calls back the results.

### O6. Automation 2: timetable parsing

- He also automated **timetable parsing** (details still to come, Q6).

### O7. Teaching through sport

- He coached basketball after school and **used practice to teach** concepts such as **acceleration** and **the
  peak of a parabola**, among others.
- **Teachers are also expected to take on a sporting code** on top of their workload.

---

## 2. What it means for Mothusi (proposed, for the project researcher to confirm)

1. **The paperwork is a chain, not a single file.** Teacher file → HOD's subject submissions → principal's
   submissions → district. Mothusi's teacher-file bundle is the bottom of that chain. A natural extension is the
   **HOD roll-up** (all classes, all teachers in a subject) and the **principal's pack**. It's multi-agent in a
   way judges will recognise: a colleague per teacher, and one more that combines their outputs for the HOD. This
   is a candidate new target for high-value-targets.md, pending Q3–Q4.
2. **The school's own fix for "capture" is a design rule.** The school spread a scarce helper across everyone
   rather than letting one teacher keep it. Mothusi should run as a **shared, school-wide colleague**, with the
   Planner allocating capacity across teachers by deadline (PoA dates) and load. Each allocation decision is
   written down: a visible "it reasons" moment.
3. **Under stress the line bent for teaching but held for marking.** An assistant taught Maths for a full week,
   because the work had to be done. But marking kept the official split: assistants marked informal work, and
   **teachers marked formal tests and exams** (Q8, corrected). Mothusi follows the same split: formal marks stay
   **proposals the teacher confirms**, as in curriculum-assistant-role.md §6. This
   is how the school actually worked, not just the policy on paper.
4. **The conversions story is the mentor's level A, done in the field.** A phone, Termux and a `while` loop cut a
   job from 3–4 people to 2. That's the demo's opening line: "I already automated the arithmetic with a phone.
   Mothusi does the rest of the cycle." It also proves the mark-sheet calculator matters in real schools.
5. **More helpers didn't make the work go away** (student teachers, assistants, extra people for conversions).
   The fix that worked was software. That supports pitching Mothusi as capacity, not headcount.
6. **Tailored explanation is a real need (IT class).** The learner note and the catch-up plan should explain at
   the learner's level and in their context. A later learner-facing step could let a learner ask "explain my
   mistake", grounded in their own script and the memo.
7. **Context makes concepts land (basketball).** Feedback and practice sets can use examples from the learner's
   world, such as a jump shot for the vertex of a parabola, or acceleration in a fast break. That's a small,
   human touch for the demo.
8. **The sporting code is part of the workload.** Teachers coach after hours too. Any time Mothusi frees up goes
   back into teaching, coaching and learner support, not into more paperwork.

### Added after the project researcher's answers (2026-11-04)

9. **Trust comes from checking against the hand method.** Resistance to the phone script disappeared once the
   team re-did the calculations by hand and they matched. Mothusi should start every new school the same way: a
   **"check a sample by hand" step** at onboarding, where the teacher re-marks a few scripts and sees the match.
   The moderation sample doubles as that check. This is how the mentor's adoption metric gets earned.
10. **Pre-moderation happens, and papers get rejected.** An HOD rejected a teacher's Maths paper as too hard.
    Mothusi's setter and pre-moderation step (target 3) catches that first. Code checks the paper against the CAPS
    weighting (about 20% knowledge, 35% routine procedures, 30% complex procedures, 15% problem solving: *CAPS FET
    Mathematics*, Section 4; 2021 exam guidelines) and tells the teacher which questions push it over, before the
    HOD sees it. It's also a ready demo moment.
11. **Everyone accounts for results, even good results.** The principal accounts to the district for the pass
    percentage, and teachers account for learner performance. Mothusi's item analysis and catch-up plan *are* that
    account, written as the work happens. A **results-analysis report** that rolls up teacher → HOD →
    principal is a strong target (see implication 1).
12. **Results live on paper.** The output must print cleanly as the school's paper mark sheet, and the intake
    should accept a **photo of a handwritten mark list**, not only typed data.
13. **Sport vs marking is a real staffroom trade-off.** Teachers argue about whether coaching time costs
    learners' marks. Mothusi doesn't take a side. It gives back marking hours, which is what makes room for both.

### Added after the second round of answers (2026-11-04)

14. **The school drew the same line Mothusi draws.** Assistants first-marked **informal** tests and teachers
    reviewed them; **formal tests and exams were marked by the teachers**, and the principal said exams were the
    teachers' responsibility. Mothusi copies that split, which schools already trust. For informal work, it marks
    and the teacher reviews, as the school did. For formal work, it proposes and the teacher decides. It adds what
    the human version lacked: a **blind** second mark on a sample, a written reason for every change, and a record
    of whether marks moved (the project researcher "has no idea if the marks changed", and nobody tracked it).
15. **Memos must stay hidden from learners.** The memos were accessible only to him. Any learner-facing feature
    (a learner asking "explain my mistake") must never expose the memo: a hard access-control rule, and a
    security test.
16. **Daily cover from a paper timetable is a recurring, unsolved job.** Every day someone works out who's
    absent and which classes are uncovered, then sends helpers there, starting from a paper timetable. He tried
    OCR and it didn't work on his phone. It's the mentor's test exactly (paper, every day, done by hand). It's a
    **candidate target: a daily cover planner** (read the timetable once, take today's absences, list the
    uncovered periods, propose who covers each, notify by WhatsApp, after approval). It's the same shared-capacity
    Planner as implication 2. Provisionally scored in high-value-targets.md §3.
    TALIS backs the burden: 21% of SA teachers call "extra duties due to absent teachers" a stressor "a lot"
    (Figure 82, p. 150), and principals reported an average of 1.46 teachers absent on the sampled Tuesday
    (TALIS 2024 Country Report, school profile section).
17. **Reading paper tables is the hard part.** His OCR attempts failed. That matches the plan to use a vision
    model with uncertainty flags the teacher confirms, rather than plain OCR.
18. **Misconceptions are subject-specific and predictable; examples are personal.** In IT: control flow and
    loops. The IT pack's misconception list starts there, and CAPS IT's trace tables are the natural way to show a
    loop step by step. The anime example shows that explanations land when they use the learner's interests.
    Mothusi may use an interest **only if the teacher or learner supplies it** (POPIA), never inferred.
19. **Extra cover is seasonal.** Student teachers covered about 3 months of the year. For the other ~7 months
    the load fell on teachers and assistants. The suspension of the assistant programme in 2026 removes the
    other half.
20. **Sport rests on very few people.** Few teachers coached, and an assistant carried a whole code's admin.
    The sport admin work (team lists, consent, transport, results) is a fair "beyond the classroom" item for the
    roadmap (curriculum-assistant-role.md §4).

### Added after the third round of answers (2026-11-04)

21. **The teachers who cope already use spreadsheets; help is needed most by those who don't.** Teachers
    comfortable with Excel "hardly relied on" assistants. Mothusi should meet both groups: **import and export
    Excel/CSV** for the first (no new habit), and **photo in, paper and PDF out** for the second. The second group
    gains the most, which is also the equity point.
22. **The school day leaves no slack.** From 08:00 to 14:30 is 45-minute lessons with two breaks, then sport.
    Marking and paperwork happen in gaps and after hours. Mothusi should run as a **batch** (submit in the afternoon,
    results waiting in the morning), not something a teacher has to sit and watch.
23. **Software caught people's mistakes, and checking by hand built the trust.** That's the order the demo
    should follow: Mothusi's arithmetic check catches an error in a hand-filled mark list, then the teacher spot-checks
    a sample and sees Mothusi was right.

## 3. Social impact (for criterion 5, "serves education")

- **Who:** teachers in public schools, already stretched, with assistants as the stop-gap. The assistant
  programme is reported suspended in 2026 (curriculum-assistant-role.md §1).
- **What it costs today:** extra hands borrowed from wherever they can be found (assistants, student teachers,
  3–4 people for one calculation job), assistants pulled into teaching, and a principal's file handed to a
  first-week assistant.
- **What changes:** the same cycle done faster, checked more consistently, and with the official boundary kept,
  so teachers spend their time on learners and coaching.
- **Gauteng angle:** this happened in a fee-paying public school in North West, not in one of the poorest. Gauteng's own figures
  (gauteng.md) show the same pressure at larger scale: 60–70 learners per teacher in township and
  inner-city schools.

## 3a. Story lines for the pitch (first person, school anonymous)

- *"I was a curriculum assistant at a public school in North West. In my first week I filed the principal's
  report to the district."*
- *"We weren't assigned to subjects. We went wherever a teacher was missing, worked out each morning from a paper
  timetable."*
- *"One week, three classes of about 40 needed their marks converted to assessment weights, and we were short
  of staff. The plan was to put three or four people on it. I wrote a Python script on my phone instead. Two of
  us did what would have taken four, and it caught mistakes in our hand calculations. People only trusted it
  after we re-did the sums by hand. Mothusi is that script, grown into a colleague, and it earns trust the same
  way."*
- *"The school kept the line: we marked informal tests, and teachers marked formal ones. Mothusi keeps that line
  too."*
- *"Teachers here also coach a sport, and they have to account for every pass rate. Mothusi gives them their
  marking hours back."*

## 4. Links to the official record

- Assistant duties and the formal-marking line: curriculum-assistant-role.md §2.1.
- REAP found assistants reduced teacher workload, stress and burnout (dbe-teachers-site.md §2, item 12).
- TALIS: marking and administration are where SA teachers exceed the OECD most ([talis-2024-findings.md](talis-2024-findings.md)).

## 5. Open questions (answers go under each)

See the list sent on 2026-11-04. Numbering is kept so answers can be filed here.

1. School context: province, district, school type and quintile, rough size and class sizes; when, and for how long.
   - **Answer (2026-11-04):** **North West**. A **fee-paying** public school. Quintile "I think it was 3", not
     certain. ⚠ Quintiles 1–3 are normally **no-fee** schools, so either it was quintile 4–5, or it was
     quintile 3 and charged no fees. The EMIS masterlist in `sources/dbe/school-admin/` has `Quintile` and
     `NoFeeSchool` columns per school, so the project researcher can check privately. District, size, class sizes and dates
     are still open.
   - **What the masterlist shows (2025 data, North West public schools):** every Q1–Q3 school is "No Fee"
     (Q1 486, Q2 298, Q3 480), and every fee-charging school is **Q4 (130) or Q5 (12)**. So a fee-paying North
     West public school is almost certainly **quintile 4** (or 5). It's waiting on the project researcher's private check.
   - **Confirmed (the project researcher, from the masterlist):** **quintile 4, fee-charging.** The `Urban_Rural` field is `99`
     (not recorded in EMIS).
2. Can the story be told in the pitch, in the first person, with the school kept anonymous?
   - **Answer:** yes, **keep the school anonymous**.
3. The principal's file: contents, audience, how often, how long it took.
   - **Answer:** it went to the **district**. **The principal prepared the documents; the project researcher filed them.**
     The principal said **she had to account for the pass percentage even though it was high.**
4. HOD submissions: what, to whom, how often; did he see teacher files checked?
   - **Answer:** he saw **HODs collect learner books and files for moderation**. He also saw **an HOD reject a
     Maths paper a teacher had set, as too hard.** (What HODs then submitted onward wasn't visible to him.)
   - **Teachers too** "often complained about the workload and having to account for the performance of the
     learners", while still being **mandated to adopt a sporting code** "when they could use that time to
     improve a child's performance". It was an endless staffroom debate, since some recognised the value of sport.
5. The conversions: what was converted to what; the task and grade; number of learners; time before and after;
   where results went; errors caught.
   - **Answer:** a **conversion to the programme-of-assessment weight**. **Three marks in** (two of them added
     into one), so **two marks, then weighted**. **Results were written on paper.**
   - **Validation:** "the script caught errors during testing and validation"; **re-doing the manual
     calculations to confirm validated the script and removed resistance.** (Q5b below: were the errors in the
     script or in the hand calculations?)
   - **Timing:** by hand, **about 45 minutes to cover a fifth** of the work, alone in the first lesson (he didn't
     yet know where the rest of the team was). He found the team **during the second lesson**. **The script
     existed by the third lesson**, and they **finished early into the fourth lesson**.
   - **Read-out (estimate from the answers to 5b and 5c below):**
     - **Size:** 3 classes × about 40 learners ≈ **120 learners**. Lessons are **45 minutes**.
     - **By hand:** a fifth (about 24 learners) took one 45-minute lesson, so about **1.9 minutes per learner**
       for one person. The whole job would be about **3 h 45 min** of one person's time.
     - **With the script:** it existed by lesson 3, and the job **finished early in lesson 4** with **2 people**.
       The remaining ≤ 96 learners took about one lesson plus a few minutes (call it 50 min).
     - **What that compares to (checked 2026-11-04, second session):** by hand, 96 learners is about 180
       person-minutes. Two people by hand would have needed about 90 min; the four the team proposed, about
       45 min. Two people with the script took about 50 min. So the honest claims are: **two people did the work
       of four** (half the people, same time), and **per person, about 1.8× the throughput**. "Three times
       faster" only holds against one person working alone, which is not what the team proposed.
     - **And it was more accurate:** testing caught errors **in the hand calculations** (5b).
     - Caveat: how much the team did by hand in lesson 2 isn't known, so the remaining work may have been less
       than 96 learners, which would shrink the gain further. Say "two of us did what would have taken four,
       and it caught our mistakes".
   - The script itself **no longer exists**. It can be rebuilt from this description as a demo prop.
   - 5b. Did testing catch errors **in the script** (fixed before use), or errors **in the hand calculations**
     (the script found human mistakes)? The pitch line depends on which.
     - **Answer:** **errors in the hand calculations.** The script found human mistakes, and re-doing the
       calculations by hand confirmed the script was right.
   - 5c. About how many learners (three classes of about how many?), and how long is a lesson at that school?
     How much was done by hand before the script took over?
     - **Answer:** about **40 learners a class**, **45-minute lessons**. The school day ran from about **08:00 to
       14:30**, with a long and a short break. (How much was done by hand in lesson 2 isn't known.)
   - Also: **teachers who could handle the workload used Excel spreadsheets and hardly relied on the
     assistants.**
6. The timetable parsing.
   - **Answer:** the timetable arrived **on paper**. **Every day** the assistants had to work out **which teacher
     wasn't in**, to deploy assistants to those classes. For **invigilation** they were always told ahead of
     time. The parsing was **for his own use**. He started with **online OCR tools**, then tried running OCR **on
     his phone**, but Termux "wasn't playing ball".
7. The Maths week.
   - **Answer:** he doesn't know why it happened. **The teacher prepared the lesson plans and memos.**
8. Physical Sciences marking and the colleagues' scripts.
   - **Answer:** the Physical Sciences exercises were marked **against memos that only he could access**. His
     colleagues marked **formal tests**. **The principal made it clear that exams were the teachers'
     responsibility.** He believes **the teachers reviewed the scripts afterwards**, so in effect a
     **two-stage process: assistant first mark, then teacher review**. He doesn't know whether marks changed.
   - **Correction (the project researcher, 2026-11-04):** his colleagues marked **informal tests**, not formal ones. **Formal
     tests and exams were marked by the teachers.** The two-stage process (assistant first mark, teacher review)
     applied to the informal tests.
9. The one-year SACE course.
   - **Answer:** a one-year course for **degree holders to bridge into teaching**: the **PGCE** (Postgraduate
     Certificate in Education), confirmed by the project researcher on 2026-11-04. SACE registers teachers rather than running
     courses, so "SACE course" was shorthand. His colleagues were graduates becoming teachers.
10. Student teachers.
    - **Answer:** they came **twice a year, for about a month and a half each time** (about 3 months of the school
      year). For the rest of the year there was no such cover.
11. The IT class.
    - **Answer:** learners most often misunderstood **control flow and loops**. With one learner who liked
      **anime**, he explained a loop as **going through a number of weapons to find the one fated for him**.
12. Sport.
    - **Answer:** **very few teachers coached**. **Soccer and netball** were the centrepieces until he arrived and
      put **basketball** at the forefront. **He handled all the basketball admin except setting up fixtures.**
13. Artefacts he still has, without learner personal data (the converter script, blank templates, a timetable).
    - **Partial answer:** the converter script no longer exists (Q5). Blank templates and the paper timetable:
      still open.
