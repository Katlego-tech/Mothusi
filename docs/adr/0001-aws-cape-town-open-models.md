# ADR 0001 — Host on AWS af-south-1 with open-weight models on an EC2 G4dn (option A)

**Status:** Accepted · **Date:** 2026-11-10 · **Decided by:** Katlego Tlhapiso · **Supersedes:** the implementation plan's
"demo laptop; optional Render + Vercel + Supabase" deploy target and Anthropic as the marking provider

## Context

Mothusi processes minors' school records, so POPIA's rules on children (s34–35) and on sending data abroad
(s72, s57(1)(d)) apply. The event preparation checklist asks for a live URL. The audit of 2026-11-08
(`docs/dropped-technologies.md`) found the deployment, handwriting reading and model choices never reached
the implementation plan. On 2026-11-10 five hosting options were drawn, checked against AWS documentation and priced
from AWS's Cape Town price lists: docs/architecture/mothusi-aws-map.html.

Facts that shaped the choice (sources and dates on that page, §11):

- In af-south-1, Bedrock offers Claude only through global cross-Region inference, which can process a
  prompt "in any supported AWS commercial Region worldwide". No Bedrock model runs in-region.
- Textract has no Cape Town or African Region. The GPUs offered in af-south-1 are G4dn (NVIDIA T4,
  16 GB) and Inf1, and Inf1 cannot serve decoder models.
- A new AWS account starts with 0 vCPUs of "Running On-Demand G and VT instances" quota.
- g4dn.xlarge costs $0.698/h in af-south-1 (price list published 2026-11-05).

## Options

| Option | Models | What leaves SA | Event cost (defaults) |
|---|---|---|---|
| **A** | Open models on our EC2 G4dn in Cape Town | No learner data; only the teacher's voice | $22 (R363) |
| B | Open models on Bedrock in Ireland | Answer crops + pseudonymised text | $17 (R278) |
| C | Everything on a school machine (Ollama) | Nothing | $0 on AWS |
| D | Claude on Bedrock's EU profile | Answer crops + pseudonymised text | $52 (R843); R49 per set, over R40 |
| E | Cape Town page reader, Claude marks | Pseudonymised text only | $43 (R696) |

## Decision

**Option A.** Everything runs in AWS af-south-1:

- CloudFront and S3 for the console, uploads, bundles and learner note pages.
- An Application Load Balancer in front of ECS on Fargate, running the core, the intake worker and the
  knowledge service.
- RDS for PostgreSQL 17 with pgvector.
- Cognito for teacher sign-in, Secrets Manager and KMS, and CloudWatch with CloudTrail.
- An EC2 g4dn.xlarge running Ollama with the models below.

AWS CDK (Python) in `infra/` defines the stack. GitHub Actions builds the images into ECR. The stack is
started for testing and for the judging window, and stopped in between.

| Job | Model (Ollama tag) | Licence |
|---|---|---|
| Page reader | `qwen3-vl:8b-instruct` (amended 2026-11-12, see below; fallbacks olmOCR-2-7B, Qwen2.5-VL-7B) | Apache-2.0 |
| Setter, Marker, Analyst, Support, Writer, Reviewer | `qwen3:14b` | Apache-2.0 |
| Moderator (blind, different family) | `phi4` (14B) | MIT |
| Embeddings (`services/knowledge`, CPU) | `bge-m3` | MIT |

`mothusi_llm`'s Ollama provider serves the G4dn unchanged (`OLLAMA_URL`). The laptop runs the same
models as the offline fallback (option C). The Anthropic adapter stays in the code and is not deployed.
Gemma 3 and Gemma 4 are avoided because they are unstable in fp16 on a T4.

The following were adopted with this decision, as recommended on the architecture page (§09):

- Ollama first, and vLLM only if evaluation shows a set takes longer than 15 minutes.
- Cognito with invited accounts only.
- No NAT gateway: tasks get public IPs behind security groups.
- Printed learner notes with a QR code to a one-time signed note page; parent email through SES later.

## Consequences

- **POPIA.** No learner record, page image or pseudonymised answer text is sent outside South Africa,
  so s72 applies only to the voice (ADR 0003). The rest of POPIA still applies: s19 safeguards
  (KMS, private data stores, Cognito, CloudTrail) and s20–21 operator terms with AWS.
- **Quality is unmeasured.** No model card publishes a handwriting or memo-marking score. The rules in
  code turn bad marks into logged retries, and evaluation measures agreement and reader accuracy before the
  demo.
- **Speed is unmeasured.** On one T4, Ollama loads one model at a time. The 30-second Marker limit
  (implementation non-negotiable 2) is re-measured in evaluation. A g4dn.12xlarge (4 T4, $5.19/h) keeps all models
  loaded if needed.
- **Cost.** About $0.70 an hour of GPU plus $0.29 an hour for the base stack while running, so rand per
  set is GPU minutes plus the stack. The implementation plan's meter must price GPU time instead of tokens.
- **Quota risk.** The G-instance quota request must be filed before building. If it is not granted by
  Wednesday 11 Nov evening, the live URL falls back to option B or D, and C remains the offline path.
  That fallback would be a new ADR.

## Alternatives not taken

Each option is kept for reference, with how to switch to it, in
docs/architecture/hosting-options.md.


B, D and E send learner-derived text out of the country, and D also breaks the R40 budget. C has no
live URL and runs on the laptop's CPU only. Google Cloud (dropped-technologies row 2) is not used: AWS
was chosen on 2026-11-10, and the Google note is kept as history.

## Amendment, 2026-11-12 (Katlego)

The page reader is `qwen3-vl:8b-instruct`, not `qwen3-vl:8b`. On the account, `qwen3-vl:8b` kept
thinking with `think: false` (a one-line JSON reply took 97 s and 3,274 tokens on the T4); the
`8b-instruct` tag is the same model's non-thinking variant (6.2 GB) and gave the same reply in 9
tokens. Every role now runs with thinking off (llm-seam.md, OllamaProvider).
