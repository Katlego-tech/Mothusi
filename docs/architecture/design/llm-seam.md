# Design — the model provider seam

**Status:** agreed · **Owner:** the project researcher (Claude builds the provider implementation; Codex builds the usage meter) ·
**Spec:** the product requirements US1 (retries), system criterion "rand per script"

---

## 1. What this covers

One interface for every model call Mothusi makes, so agents never import an SDK and the demo can run
against recorded answers or a local model. It covers the `Provider` protocol, the `Usage` record,
the replay provider, the usage meter and the pricing data. It does not cover prompts (each agent's
module) or retries on failed checks (the Planner, [marking-pipeline.md](marking-pipeline.md) §4).

## 2. Reference material

| Kind | Where |
| --- | --- |
| The pattern | Alfred's `packages/llm` (provider-agnostic chat roles, usage meter), FrameFlow's provider seam (`auto`/gemini/openai_compat/ollama_native) |
| SDKs, pinned | `anthropic==2.6.0`, `httpx==0.28.1` (Ollama's HTTP API) in `packages/llm/pyproject.toml` |
| Existing code to match | `packages/llm/src/mothusi_llm/protocol.py` (the Protocol, `Usage`, `ProviderError`) |
| Pricing | Anthropic's published per-token prices and a ZAR/USD rate, each with its date, in `packages/llm/pricing.toml` (the usage meter reads; the driver fills from the pricing page, never from memory) |

## 3. Domain model

`Usage(model, input_tokens, output_tokens, cost_zar)` with `total_tokens`; `sum_usage(...)`.
`ProviderError(message, retryable: bool)`. All in `protocol.py`.

## 4. Flow

An agent builds `system` and `user` strings and names the contract model it wants back. The
provider asks the model for JSON, validates it with `output.model_validate_json`, and returns the
model plus `Usage`. A response that is not valid JSON for the model is a `ProviderError(retryable=True)`
(the Planner re-briefs once with the validation error appended, then falls back); a 4xx from the API
is `retryable=False`; a timeout or 5xx is `retryable=True`. The fallback order is the Planner's,
not the provider's: `anthropic` → `ollama` → `needs_teacher`.

## 5. State

None.

## 6. Contracts

```
class Provider(Protocol):                                        (exists, protocol.py)
    name: str
    async def complete[T: Contract](self, system: str, user: str, output: type[T],
                                    *, budget_tokens: int | None = None) -> tuple[T, Usage]

type Pricer = Callable[[str, int, int], Decimal]                (protocol.py, the provider implementation)
    (model, input_tokens, output_tokens) -> rand. Injected, so the adapters do not wait on the
    meter: once the usage meter lands, the caller passes functools.partial(price, load_pricing(path)).

AnthropicProvider(model: str, api_key: str, timeout_s: float = 30, *,
                  price: Pricer, client: AsyncAnthropic | None = None)
    name = "anthropic"; Messages API with structured output (output_config.format = json_schema
    from anthropic.transform_schema(output.model_json_schema())) plus a JSON-only instruction in
    the system prompt, and `max_tokens = budget_tokens or 16000` (thinking is on for the current
    models and counts against max_tokens; 4096 truncates). The reply's text block is still validated
    with output.model_validate_json, because the schema cannot carry the contract's validators.
    Usage: input = input + cache-creation + cache-read tokens (all priced as input: an upper bound),
    output as reported, cost = price(model, input, output). `client` is for tests (a mock transport).
    The default client has max_retries = 0: the Marker's 30 s budget (the project’s grounding rules) holds only if the
    SDK does not retry behind the Planner's back.
    Errors: 429, 5xx, timeouts and connection errors are retryable; any other 4xx is not; a
    `refusal` stop is not (the Planner falls back to ollama); no text block, a `max_tokens` stop or
    JSON that does not fit the model is retryable.

OllamaProvider(model: str, base_url: str = "http://localhost:11434", timeout_s: float = 180, *,
               client: httpx.AsyncClient | None = None)
    name = "ollama"; POST /api/chat with stream = false, think = false (every role, the project researcher
    2026-11-11: qwen3 thinks by default, ~21 tok/s on the T4 put every Marker call past the 60 s
    timeout in the first eval; with thinking off a one-question mark took 1.4 s instead of 11 s),
    format = the model's JSON schema and, when budget_tokens is set, options.num_predict = budget_tokens; Usage from prompt_eval_count and
    eval_count with cost 0. Timeouts, 5xx and a reply that is not a chat message are retryable; 4xx
    (an unpulled model) and a refused connection (Ollama not running) are not. timeout_s = 180
    because the clock includes time queued on the host: a Marker call alone takes ~37 s on the T4,
    and four decoding together take up to ~120 s (measured 2026-11-12). The Planner keeps any
    more from reaching the host: every call holds one of its Slots (marking-pipeline.md §6.2),
    so a call waits in the core, outside this clock, instead of queuing on the host.

ReplayProvider(path: Path, price: Pricer)
    name = "replay"; reads a JSONL file of records
      {"key": "<sha256 of system + "\\n--\\n" + user + "\\n--\\n" + output.__name__>",
       "model": "<model name>", "output": <JSON object>, "input_tokens": int, "output_tokens": int}
    complete() finds the record by key, validates `output` into the requested model, and returns
    it with Usage priced by the injected Pricer (the same injection as the other adapters, §8;
    nothing is read from disk at call time). A missing key is ProviderError(retryable=False) naming the
    first 12 characters of the key. record(path, system, user, output_model_name, model, obj,
    input_tokens, output_tokens) appends a record (what the eval run uses to capture fixtures).

Pricing / meter
    pricing.toml:
      [rates]  zar_per_usd = <Decimal>  source = "<where and when>"
      [[model]] name = "<model>" usd_per_million_input = <Decimal> usd_per_million_output = <Decimal> source = "<URL, date>"
    load_pricing(path: Path) -> Pricing
    price(pricing: Pricing, model: str, input_tokens: int, output_tokens: int) -> Decimal
      ROUND_HALF_UP to 4 dp in rand; an unknown model is a ValueError naming the models known.
    Meter: add(usage) / total() -> (input, output, cost_zar) / per_script(n) -> Decimal (2 dp).
```

## 7. Structure

| Path | New? | Responsibility |
| --- | --- | --- |
| `packages/llm/src/mothusi_llm/protocol.py` | exists | `Provider`, `Usage`, `sum_usage`, `ProviderError`, `Pricer` |
| `packages/llm/src/mothusi_llm/anthropic_provider.py` | new | `AnthropicProvider` |
| `packages/llm/src/mothusi_llm/ollama_provider.py` | new | `OllamaProvider` |
| `packages/llm/src/mothusi_llm/replay.py` | new | `ReplayProvider`, `record` |
| `packages/llm/src/mothusi_llm/meter.py` | new | `Pricing`, `load_pricing`, `price`, `Meter` |
| `packages/llm/pricing.toml` | new (the driver, with the eval run's eval run; row 4 of docs/resources-to-fetch.md) | rates and per-model prices with sources and dates |

## 8. Decisions & alternatives

| Decision | Chosen | Rejected, and why |
| --- | --- | --- |
| Structured output | Validate JSON into the pydantic contract; invalid is a retryable error | Free text parsed by regex: silent partial parses are how invented marks get in |
| Pricing | A data file with sources and dates | Constants in code: prices change and nobody dates a constant |
| Replay keying | sha256 of the full prompt and the output type | Sequence numbers: one reordered call misaligns every later fixture |
| Pricing in the adapters | An injected `Pricer` | Importing the meter: the provider implementation would wait on the usage meter, and tests would need a pricing file |
| Anthropic JSON | Structured output (json_schema) and our own validation | Instruction only: a model can still wrap JSON in prose; `messages.parse` alone: its validation error would escape as a pydantic error, not a `ProviderError` |

Deviations from architecture-defaults.md: none.

## 9. How this is verified

- Replay: a recorded `Mark` comes back validated; a tampered record that no longer fits `Mark` is a
  retryable `ProviderError`; a missing key is non-retryable.
- Meter: a known model prices to the worked figure in the test (from the fixture pricing file);
  unknown model raises; totals and per-script agree with `sum_usage`.
- Real providers: behind a pytest marker, skipped without a key; one smoke call each in the eval run.

## 10. Open questions

- [x] **Option A (ADR 0001, 2026-11-10):** settled in §11 (providers per role, image input, hosting cost).

- [ ] Whether the Moderator should run on a different model family from the Marker (a stronger blind
      check). The seam makes it a configuration choice; decide with the eval run.

---

## 11. Option A changes (ADR 0001, ADR 0002) — draft 2026-11-10

tests and replay recordings keep working untouched.

### 11.1 Image input

```
class Provider(Protocol):
    name: str
    async def complete[T: Contract](self, system: str, user: str, output: type[T],
                                    *, budget_tokens: int | None = None,
                                    images: Sequence[bytes] = ()) -> tuple[T, Usage]
```

- `images` are PNG or JPEG bytes, in order, attached to the user turn.
- `OllamaProvider` sends them as `messages[-1].images` (base64), which is Ollama's native `/api/chat`
  field.
- `AnthropicProvider` sends them as image content blocks before the text. It is kept for the
  documented options D and E (docs/architecture/hosting-options.md).
- `ReplayProvider` folds `sha256(each image)` into its key, so a recording made with images replays
  only with the same images. The key is unchanged when `images` is empty, so existing recordings still
  match.
- A provider that cannot take images raises `ProviderError(retryable=False)`.

### 11.2 Providers per role

```
type Role = Literal["reader", "text", "moderator"]
class ProviderSet(Contract-like frozen dataclass):
    reader: Provider; text: Provider; moderator: Provider; fallback: Provider | None; priced: bool
    def for_agent(self, agent: str) -> Provider
        "read" -> reader; "moderate" -> moderator; every other agent -> text

from_environment() -> ProviderSet      (replaces the tuple; providers.py, the implementation)
    MOTHUSI_PROVIDER = ollama | anthropic | replay  (unchanged meaning)
    ollama:   OLLAMA_URL (default http://127.0.0.1:11434; on AWS the G4dn's private address)
              MOTHUSI_OLLAMA_MODEL           default "qwen3:14b"   -> text
              MOTHUSI_OLLAMA_MODERATOR_MODEL default "phi4"        -> moderator
              MOTHUSI_OLLAMA_READER_MODEL    default "qwen3-vl:8b-instruct" -> reader
              fallback: OLLAMA_FALLBACK_URL if set (the laptop), else None
    replay:   one ReplayProvider for all three roles (unchanged)
    anthropic: kept for options D/E; reader and text = MOTHUSI_ANTHROPIC_MODEL, moderator =
              MOTHUSI_ANTHROPIC_MODERATOR_MODEL; fallback = the ollama set
```

Each role is still wrapped in `Fallback`, `Metered` and `Recorded` exactly as today, so the Planner's
fallback events and the meter are unchanged. The Moderator's different family (phi4 against qwen3) is
now configuration, which settles §10's first open question for option A.

### 11.3 Hosting cost (rand for GPU time)

Under Ollama a call has no per-token price, so a set's cost is the time the stack runs. The contract
is **[eval.md](eval.md) §6**, adopted here:

- `pricing.toml` gains `[[instance]]` (for example g4dn.xlarge at $0.698/h) and `[stack]` ($0.289/h),
  each with its source and date.
- `meter.py` gains `InstancePrice`, `StackPrice`, `Pricing.instance` / `.stack` and `instance_price`.
  Old files without these sections still load.
- `run_cost` turns wall time into rand: 12.00 minutes on a g4dn.xlarge at R16.3515 is R3.23.

Ollama calls stay at R0 each, so nothing is double-counted. The eval's and the Planner's usage report
put the run's rand beside the token totals, and that figure is checked against the architecture plan's R40 per set.
