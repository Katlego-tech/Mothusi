# ADR 0003 — A voice colleague on ElevenLabs Agents

**Status:** Accepted · **Date:** 2026-11-08 (recorded as an ADR on 2026-11-10) · **Decided by:** Katlego
Tlhapiso

## Context

Katlego decided on 2026-11-08 to use ElevenLabs Agents ("we are definitely using that"). The decision
sat on an unmerged branch until 2026-11-10 (dropped-technologies row 1). Katlego's answers fix its scope:

- all four tasks: report run progress, explain a mark, ask Mothusi's questions, brief the results;
- in the console;
- English only;
- one scripted demo beat.

## Decision

The voice runs in the teacher console through ElevenLabs Agents, acting only through Mothusi's API with
client tools. Its endpoints are name-free, and every sentence about a mark is built by Mothusi's code (a
`say` string), never phrased by the agent's model. It cannot approve or send. Design:
docs/design/voice-colleague.md (draft).

## Consequences

- **The one cross-border flow under ADR 0001.** The teacher's speech, and replies that carry pseudonym
  codes and marks (never names or page images), go to ElevenLabs. ElevenLabs stores data in the US by
  default, offers no Africa residency, and limits zero-retention mode to Enterprise. The basis is the
  teacher's consent (s72(1)(b)) for her own voice. **Open for Katlego, deferred by Katlego on 2026-11-10 ("we will address ElevenLabs later"):** whether
  "explain a mark" may send a learner's pseudonymised mark and quote abroad, or must stay class-level.
  The voice implementation does not ship "explain a mark" by voice until this is decided; the other three voice tasks are
  unaffected.
- The agent's conversation model runs at ElevenLabs, outside `mothusi_llm`'s meter.
- Amazon Transcribe and Polly both run in af-south-1 if voice ever has to stay in South Africa.
