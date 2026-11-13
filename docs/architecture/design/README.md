# Design documents

One per non-trivial lane, named after it, so `feat/<lane>` <-> `docs/architecture/design/<lane>.md`.

Diagrams are **Mermaid in fenced code blocks**, never images -- they diff in a PR and every
assistant can read and write them.

| Lane | Doc | Status | Covers |
| --- | --- | --- | --- |
| `contracts` | [domain-model.md](domain-model.md) | agreed | class diagram of every entity, the invariants the models enforce, mark-sheet number semantics |
| `core` (pipeline) | [marking-pipeline.md](marking-pipeline.md) | agreed | sequence of the ten agents and the rules between them, failure paths, the assessment state machine, Brief/Result envelopes |
| `rules` | [rules-engine.md](rules-engine.md) | agreed | verbatim signatures and semantics of every deterministic check (the Codex lane) |
| `intake` | [intake.md](intake.md) | agreed; §11 draft 2026-11-10 (photos and PDFs, ADR 0002) | memo TOML and scripts CSV formats, pseudonyms, `load_memo` / `load_scripts` (Codex) |
| `llm` | [llm-seam.md](llm-seam.md) | agreed; §11 draft 2026-11-10 (images, providers per role, GPU-time cost) | the `Provider` protocol, Anthropic and Ollama adapters (Claude), replay provider and meter (Codex) |
| `outputs` + `delivery` | [outputs-and-delivery.md](outputs-and-delivery.md) | agreed; option A additions draft 2026-11-10 (PDF report, note links, answer sheet) | the bundle on disk, the XLSX mark sheet, the Mailer protocol and the Maildir mailer (Codex) |
| `events` | [event-log.md](event-log.md) | agreed | the Event record, the JSONL EventLog (Codex), the one-line rendering |
| `infra` | [deployment-aws.md](deployment-aws.md) | draft 2026-11-10 | AWS af-south-1 stacks (CDK), security groups, IAM, the G4dn model host, start/stop runbook, CI deploy (ADR 0001) |
| `knowledge` | [knowledge.md](knowledge.md) | draft 2026-11-10 | pgvector retrieval over the held DBE documents with bge-m3 on CPU, page-exact citations, the citation check |
| `eval` | [eval.md](eval.md) | draft 2026-11-10 | revised eval contract: agreement, minutes, rand from GPU time, latency, reader accuracy |
| `voice` | [voice-colleague.md](voice-colleague.md) | draft 2026-11-08 | ElevenLabs Agents voice: name-free endpoints, client tools, state machine (ADR 0003) |
| `web` (console) | [console-ui.md](console-ui.md) | agreed; §11 draft 2026-11-10 | the HTTP + SSE contract and event kinds, the run view reducer, component tree, four screens drawn in `console-ui/` (PNG + HTML source) |
| `learner` | [learner-section.md](learner-section.md) | draft 2026-11-11 | learner code-and-PIN sign-in, consent, practice tests and immediate code-checked results (ADR 0004) |
| `hall` | [exhibition-hall.md](exhibition-hall.md) | draft 2026-11-11 | self-hosted, curriculum-cited narrated 3D exhibits for Maths and Accounting (ADR 0004) |
