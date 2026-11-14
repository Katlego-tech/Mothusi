# Hosting options: what was weighed, and how to switch

**Chosen: option A** ([ADR 0001](../adr/0001-aws-cape-town-open-models.md), the project researcher, 2026-11-10). The other four
are kept here for reference, and as the fallbacks ADR 0001 names. Each has its own diagram in
[mothusi-aws-map.html](mothusi-aws-map.html) §04. Facts and prices were read on 2026-11-10, with sources on that
page (§11).

All five options share everything except where the models run:

- the console (CloudFront + S3);
- the core, intake and knowledge services (ECS on Fargate);
- RDS for PostgreSQL 17 with pgvector;
- Cognito, S3 bundles and the learner side.

So switching options changes model endpoints and provider configuration, not the application.

## At a glance

| | A · open models, G4dn Cape Town | B · open models, Bedrock EU | C · school machine | D · Claude, Bedrock EU | E · hybrid |
|---|---|---|---|---|---|
| Page reader | `qwen3-vl:8b` on the G4dn | Qwen3-VL-235B, Ireland | `qwen3-vl:8b` on Ollama, CPU | Claude Sonnet 5.5 | `qwen3-vl:8b` on the G4dn |
| Marker and text agents | `qwen3:14b` | Qwen3-32B | `qwen3:14b` | Claude Sonnet 5.5 | Claude Sonnet 5.5 |
| Moderator | `phi4` | gpt-oss-120b | `phi4` | Claude Opus 5.5 | Opus 5.5, or `phi4` on the G4dn |
| What leaves South Africa | no learner data (voice only) | answer crops + pseudonymised text | nothing (voice optional) | answer crops + pseudonymised text | pseudonymised text, no pages |
| POPIA basis for model data | none needed (no transfer) | s72(1)(a): AWS terms, EU regions | none needed | s72(1)(a) | s72(1)(a), text only |
| Billing | GPU per hour ($0.698/h) | per token | the school's hardware | per token (Claude prices, EU +10%) | GPU hours + tokens |
| Event total (defaults) | $22 (R363) | $17 (R278) | $0 on AWS | $52 (R843) | $43 (R696) |
| 4-hour judging window | $3.95 | $3.36 | $0 | $13.23 | $9.77 |
| Model cost per class set | GPU minutes | R9 | none | **R49, over the R40 budget** | R24 |
| Paid by the $200 new-account credit | yes (EC2) | unverified | n/a | **no**: Claude is billed through AWS Marketplace | GPU yes, Claude no |

Defaults for the totals: the stack exists 5 days; 24 h of testing (12 h of GPU); a 4-hour judging window;
10 sets in testing and 4 while judged; 32 scripts of 4 pages per set; R16.35 per USD. The calculator on the
architecture page (§05) recomputes them.

## B · Open models on Bedrock in the EU

- **What:** Bedrock in-region models in Ireland (eu-west-1). Qwen3-VL-235B reads pages ($0.62 / $3.12
  per 1M tokens in / out), Qwen3-32B marks ($0.18 / $0.70), and gpt-oss-120b moderates ($0.18 / $0.70).
  Names and covers stay in Cape Town; answer crops and pseudonymised text cross to the EU.
- **When:** the fallback for the live URL if the G-instance quota is not granted by Wed 11 Nov evening.
- **To switch:**
  - Write a Bedrock Converse provider in `mothusi_llm`.
  - Enable the models in eu-west-1.
  - Point the agents' providers at it.
  - Record the switch in a new ADR, because learner-derived data would then leave the country.

## C · Everything on a school machine

- **What:** the same containers under `docker-compose`, with the models on Ollama on the laptop or a
  school server. Nothing leaves the building, and notes go home printed.
- **When:** it is the offline fallback for every option (the demo script), and the pitch for schools with poor
  connectivity.
- **Limit:** the demo laptop has no GPU (22 GB RAM), so a full set is slow (unmeasured).
- **To switch:** `docker compose up`, `MOTHUSI_PROVIDER=ollama`, and `OLLAMA_URL` left on localhost.

## D · Claude on Bedrock in the EU

- **What:** Claude Sonnet 5.5 reads and marks, Opus 5.5 moderates and Haiku 5.5 writes the notes. Calls
  go through Bedrock's EU geographic profile from Frankfurt (eu-central-1). Cape Town itself offers
  Claude only through global inference, which can run a prompt in any commercial region worldwide, and
  that raises s57(1)(d) for children's data.
- **When:** if the GPU quota is refused and the eval shows the open models cannot mark well enough.
- **Watch:** R49 per set, above the R40 budget; about two-thirds of that is page images. The $200 credit
  does not cover it; AWS Activate credits do, but take 7–10 business days. Prompt caching lowers the
  cost.
- **To switch:**
  - Add the Anthropic SDK's Bedrock client to `mothusi_llm`; `anthropic_provider.py` already exists.
  - Enable Claude in eu-central-1 and request a quota.
  - Record the switch in a new ADR.

## E · Hybrid: pages read in Cape Town, Claude marks

- **What:** option A's G4dn reader, with Claude doing the marking, moderating and writing. Only
  pseudonymised text crosses the border, never a page.
- **When:** if the eval run shows the open marker's agreement is too low but pages must stay in South Africa.
- **To switch:**
  - Same as D for the Claude side, with A's reader unchanged.
  - The core's `Fallback(primary, fallback)` lets Claude run first, with the G4dn's open model taking
    over automatically if Bedrock throttles.
  - Mixing per agent (for example Claude marking and `phi4` moderating) needs a provider per agent, which
    is a planned change in `mothusi_llm`.

## Also weighed

- **Google Cloud** (Cloud Run, Cloud SQL, africa-south1): not used (source note not in this repo).
- **Anthropic's own API:** it uses the same `anthropic_provider.py`, but brings a second vendor contract
  and a bill outside AWS.
- **SageMaker real-time endpoints on ml.g4dn in Cape Town** ($0.977/h): a managed variant of A.
- **Inf1:** cannot serve decoder models, so it is not an option.
- **Textract:** no Cape Town or African region; nearest are the EU and Mumbai.
