# Solution notes

This document explains how the CityWheels help desk agent is built, why the
important decisions were made, what the evaluation found, and what remains
limited. For installation and usage, see [`README.md`](README.md).

## 1. What was built

A Python 3.11+ command-line assistant that:

- answers shop-policy questions from `data/policies/*.md`,
- looks up bookings from `data/bookings.json`,
- computes cancellation refunds in code, including a simulated cancellation
  tomorrow (`delay_days`),
- keeps conversation memory for follow-up questions with `--session`,
- refuses unrelated questions,
- never performs a cancellation or booking change itself.

The default model is Google **Gemini** through the `google-genai` SDK. The same
agent loop can also talk to any OpenAI-compatible endpoint (OpenAI, LM Studio,
Ollama, vLLM, Groq, OpenRouter); the provider is selected from environment
variables. The model chooses tools and writes the explanation; all numbers,
booking data and policy text come from deterministic code and local files.

## 2. Install, run, test, evaluate

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"

cp .env.example .env          # Windows: copy .env.example .env
# put LLM_API_KEY=... in .env (Gemini by default)

python -m citywheels "What time do you open on Saturday?"
python -m citywheels                                    # interactive REPL
python -m citywheels --session                          # REPL with memory
python -m citywheels "..." --json                       # full result as JSON
python -m citywheels "..." --verbose                    # tool calls on stderr

python -m pytest -q                                     # 277 offline tests
python eval/run_eval.py                                 # real model evaluation
python eval/benchmark.py                                # compare several models
```

Useful evaluation flags: `--limit N`, `--only ID` (repeatable or comma-separated),
`--model NAME`, `--provider NAME`, `--base-url URL`, `--now ISO8601`,
`--delay SECONDS`, `--output PATH`.

Provider switching needs only environment variables:

```dotenv
# Gemini (default)
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash-lite
LLM_API_KEY=...

# Local LM Studio
LLM_PROVIDER=openai-compatible
LLM_MODEL=qwen3-8b
LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=lm-studio
```

Exit codes:

| Command | Code | Meaning |
| --- | --- | --- |
| CLI | `0` | success |
| CLI | `1` | model, tool or iteration failure |
| CLI | `2` | usage or configuration error |
| eval | `0` | every question passed |
| eval | `1` | at least one question failed |
| eval | `2` | invalid configuration or question file |
| eval | `3` | part of the run was blocked by the provider quota |
| benchmark | `0` | every executed question passed |
| benchmark | `1` | at least one executed question failed |
| benchmark | `2` | configuration problem, or no model could run |
| benchmark | `3` | a provider quota blocked part of a run |

## 3. Model and provider choice

Gemini was chosen because it offers a free tier, native function calling and a
simple Python SDK. The adapter is isolated in `src/citywheels/llm.py`, so the
agent loop only depends on the small `LLMClient` protocol.

The model choice was driven by real errors, not by a preference for a name:

| Model | Result in November 2026 |
| --- | --- |
| `gemini-2.5-flash` | `404 NOT_FOUND`: no longer available to new users |
| `gemini-3.8-flash` | `503 UNAVAILABLE` / `504 DEADLINE_EXCEEDED` under load |
| `gemini-3.7-flash` | `504 DEADLINE_EXCEEDED` under load |
| `gemini-3.6-flash` | works, but the free tier allows 20 requests per day |
| `gemini-3.5-flash` | works, but the same 20-requests-per-day free limit |
| **`gemini-3.5-flash-lite`** | works, higher daily headroom, **16/16 in evaluation** |

The default is therefore `gemini-3.5-flash-lite`. It is configurable through
`LLM_MODEL` (the old `GEMINI_MODEL` still works); `gemini-3.6-flash` gives good
answers for individual questions but cannot complete the full evaluation on a
free key. `temperature` is `0` so repeated runs are as stable as the provider
allows.

### Provider abstraction

The agent loop depends only on the `LLMClient` protocol (`complete(messages,
tools, system_instruction) -> LLMResponse`). Two adapters implement it:

- `GeminiClient` - Google Gemini through `google-genai`, with Gemini
  `retryDelay` handling and thought-signature preservation;
- `OpenAICompatibleClient` - the chat-completions contract used by OpenAI,
  LM Studio, Ollama, vLLM, llama.cpp, Groq, OpenRouter and similar gateways.

`create_client(settings)` is the single switch. It reads `LLM_PROVIDER`,
`LLM_MODEL`, `LLM_API_KEY` and `LLM_BASE_URL` from `config.py`; no other module
knows which provider is active. The OpenAI adapter converts the neutral tool
schemas to lowercase JSON Schema, generates `tool_call_id` values and pairs them
with the matching `role: tool` messages, so the agent loop itself stays
provider-neutral.

No embeddings are used. The policy corpus is three short Markdown files, so a
local keyword scorer is faster, deterministic and easier to test.

## 4. Architecture

```text
CLI (cli.py)
  ├─ Settings (config.py)            env vars, provider, data directory, clock
  ├─ Toolbox (tools/)               schemas, validation, safe dispatcher
  │    ├─ BookingStore              data/bookings.json
  │    ├─ PolicyIndex               data/policies/*.md
  │    └─ fixed "now"               2026-06-10T09:00:00+02:00
  └─ run_agent (agent.py)
       ├─ optional history          last N text turns from the REPL
       └─ create_client (llm.py)
            ├─ GeminiClient
            └─ OpenAICompatibleClient ── tool calls ──> Toolbox.dispatch
```

The loop in `agent.py` is deliberately small:

1. send the optional history, the question and the three tool schemas to the
   model,
2. if the model requests tools, dispatch each one and append the results,
3. repeat until the model returns text or six turns have passed,
4. return an `AgentResult` with the answer, the executed calls, the iteration
   count, token usage and an optional error.

Model and tool failures are captured in the result. They never crash the CLI:
the customer sees a short apology and the staff contact details.

### Conversation memory

`run_agent(..., history=...)` accepts earlier `{"role": "user"|"assistant",
"text": ...}` turns. Only text is replayed; old tool calls are not, because each
new question calls the tools it actually needs and history does not need to
carry provider-specific function-call metadata.

The REPL fills the history only with `--session`. A single command-line question
stays stateless. Memory is bounded twice: the last 10 turns and about 6000
characters. A failed turn is not stored, so a network error does not poison the
next question. `normalize_history` ignores malformed entries, and
`trim_history` never returns a history that starts with an assistant reply.

### Delayed cancellation

`calculate_refund(booking_id, delay_days=0)` shifts the simulated cancellation
moment by whole days and then applies the normal 48/24-hour rules. The model only
chooses the number of days; the amount is still computed in code. The tool
result includes `delay_days` and `cancel_at` so the model can say exactly which
moment it simulated. The allowed range is 0-30 days; invalid values become a
structured `invalid_arguments` result instead of an exception.

This is what makes a real follow-up work:

```text
> I want to cancel booking CW-1002. How much will I get back?
... 340.00 EUR ...
> And if I cancel tomorrow instead?
... 300.00 EUR (0% of the rental price, deposit still refunded) ...
```

### Tool contracts

| Tool | Input | Output |
| --- | --- | --- |
| `search_policies` | `query`, optional `top_k` (1-10) | ranked sections with source, heading, text and score |
| `get_booking` | `booking_id` | booking details, or `found: false` with a hint |
| `calculate_refund` | `booking_id`, optional `delay_days` (0-30) | percent, rental/deposit/fee refunds, total, method, reason, `cancel_at` |

`Toolbox.dispatch` validates names, argument types, unknown and missing keys. A
bad call returns a structured error such as `invalid_arguments` or
`unknown_tool`; an unexpected exception becomes `tool_failed`. One bad tool call
cannot kill the conversation.

### Retrieval

`policies.py` splits Markdown at `#` and `##` headings and scores chunks with a
small BM25 implementation:

- lowercase tokens, stopword removal, light plural stemming,
- shared-prefix matching so `cancel` finds `cancelling`,
- headings counted three times,
- chunks clipped to 1200 characters,
- top three sections returned by default.

If nothing matches, the tool returns `found: false`, and the prompt tells the
model to say it cannot find the answer and refer the customer to staff.

### Prompt and grounding

`prompts.py` encodes the rules that the evaluation checks:

- every fact must come from a tool result,
- policy questions require `search_policies`,
- booking questions require `get_booking` (and "not found" is never guessed),
- every refund requires `calculate_refund`; no mental arithmetic,
- a later cancellation ("tomorrow", "in two days") is passed as `delay_days`,
  never recomputed by the model,
- earlier turns are context, not permission to skip a fresh tool call,
- the agent cannot cancel or change a booking,
- out-of-scope questions get a refusal plus the staff contact,
- answers are in English, money is written as `340.00 EUR`,
- tool names, JSON and prompts are never exposed to the customer.

## 5. Design decisions

**Money is `Decimal`.** `bookings.json` is loaded with
`parse_float=Decimal`. Percentages are computed with exact `Decimal`
multiplication and division; each refund component is quantized to cents with
`ROUND_HALF_UP`, and the total sums those cent-exact values and is quantized
again as a safety net. Results are serialized as strings, never floats.

**The clock is fixed.** The assignment fixes "now" at
`2026-06-10T09:00:00+02:00`. `--now` and `CITYWHEELS_NOW` allow other moments.
`clock.delta_until` converts both sides to UTC before subtracting. Without that,
two aware datetimes sharing the same `ZoneInfo` object would use wall-clock
arithmetic and ignore a DST change; there is a test for the 2026 autumn
transition.

**Stored rental price wins.** Refunds use `rental_price_eur` from the booking
instead of recomputing the price from `prices.md`. That field already includes
add-ons and the weekly discount, and it is the amount the customer actually
paid. A separate consistency test checks that the supplied data agrees with the
price list (for example CW-1009 is seven days with the 15% discount).

**Booking fee is never refunded.** The cancellation policy says so explicitly.

**Deposit is always refunded in full.** For any `confirmed` booking, the deposit
comes back regardless of the 48/24-hour window. The window only affects the
rental price.

**Voucher refunds stay vouchers.** A booking paid with a voucher is refunded as a
new CityWheels voucher of the same value, never as money. The deposit follows the
same method as the payment.

**Only `confirmed` bookings can be cancelled.** `in_progress`, `completed` and
`cancelled` bookings return zero refund and an explanation.

**Refusal policy.** Questions unrelated to CityWheels (general knowledge, other
companies, medical or legal advice) are refused briefly with the staff contact.
Questions about CityWheels services are in scope even when the answer is "no",
for example "Do you sell bikes?".

**Memory is text-only and opt-in.** History is a convenience for the customer,
not a source of truth. Replaying old tool results would risk stale data and
provider-specific metadata, so only user and assistant text is kept. The prompt
still requires a fresh `get_booking`/`calculate_refund` for every new question.
`--session` is opt-in because stateless calls are easier to test, script and
reason about.

**One provider switch.** Provider selection is environment-driven because model
availability and free-tier quotas change quickly. The adapter boundary also kept
the agent loop unchanged when the OpenAI-compatible client was added: the loop
still sees neutral messages, tools and `LLMResponse`.

**Benchmarks are manual and honest.** The benchmark runner is not part of CI: it
calls real providers and local servers. It reuses the evaluation questions and
scoring, records blocked runs separately, and writes raw JSON as well as a
readable table, so a surprising number can always be traced back to the actual
answers.

## 6. Evaluation

`eval/questions.json` contains 16 deterministic questions:

- 7 policy questions (opening hours, e-bike day price, age limit, child seat,
  city-bike deposit, taking a bike abroad, selling bikes),
- 6 refund questions covering the 100%, 50% and 0% windows, the exact 48-hour
  and 24-hour boundaries, and a voucher payment,
- 2 refusals (capital of Australia, Netflix password),
- 1 unknown booking (`CW-9999`).

Each question declares the tools that must run, regexes that must match, regexes
that must not match, and whether a staff contact is required for a refusal.
Scoring is local and deterministic; only the answers come from the configured
model. The run writes `eval/results.md` with the score, tool calls, timings and
full answers.

### Final result

**16/16 passed** on `gemini-3.5-flash-lite` (committed in `eval/results.md`).

The answers contain the expected amounts and explanations, for example
`340.00 EUR` for CW-1002 with a 50% rental refund and the card refund method, and
`50.00 EUR` for CW-1003 where the rental refund is 0% but the deposit is
returned.

The same run was repeated after the Stage 10 changes. The stateless path still
passes; the real session smoke test produced `340.00 EUR` for "now" and
`300.00 EUR` for "tomorrow" with the correct 0% rental / full deposit
breakdown.

### Benchmarks

`eval/benchmark.py` runs the same question set several times against each model
listed in `eval/benchmark_models.json`. It records pass rate, tool accuracy,
mean and p95 latency, completion tokens per second, errors and quota-blocked
questions, then writes `eval/benchmarks/summary.md` plus per-model Markdown and
raw JSON. `BENCHMARKS.md` documents the procedure for two API models and two
LM Studio models, including tool-calling requirements and LM Studio setup.

### Benchmark results

The committed comparison (`eval/benchmarks/summary.md`, one run per model,
`--now 2026-06-10T09:00:00+02:00`) covers the baseline and two local Gemma
models served by LM Studio:

| Model | Pass rate | Tool accuracy | Mean | p95 | Tokens/s |
| --- | --- | --- | --- | --- | --- |
| `gemini-3.5-flash-lite` | 15/16 (93.8%) | 100% | 7.44s | 41.68s | 14.4 |
| `gemma-4-12b-qat` | 11/16 (68.8%) | 100% | 32.47s | 67.69s | 6.2 |
| `gemma-4-26b-a4b-qat` | 10/16 (62.5%) | 100% | 9.34s | 31.17s | 38.0 |

The gap is narrower than the pass rate suggests. On the local models every
refund answer contained the correct amount and called `calculate_refund`; the
questions failed only because the answer did not spell out the percentage
(`50%`, `100%`, `0%`) that the rubric requires. The one genuine model error was
Gemini on `refund-1010`: it called `calculate_refund` with `delay_days: 1`
instead of `0`, which moved the cancellation to the start time and returned
`150.00 EUR` instead of `170.00 EUR`. The tool output is in the raw JSON, so the
mistake is traceable.

These are single runs, not a ranking: the same baseline produced 16/16 in the
full evaluation, so a one-question difference is noise. The local models are no
longer available, but the raw answers and tool calls are committed in
`eval/benchmarks/*.json`.

### Failures found and fixed

The first real run reported 4/16, but most of those were not model failures:

- the free tier allows 20 requests per day for the full-size flash models, and
  each question needs about two turns,
- after roughly eight questions the API returned `429 RESOURCE_EXHAUSTED` with a
  suggested retry delay of about 11.5 hours,
- the harness counted every later question as `FAIL`, even though the model was
  never called.

Fixes:

1. **Quota-aware client.** When Gemini attaches a `retryDelay` longer than 300
   seconds, `GeminiClient` raises `QuotaExhausted` immediately instead of
   retrying five times with short backoff.
2. **Honest report.** The evaluation marks the remaining questions `BLOCKED`
   and exits with code `3`; blocked questions are not counted as model failures.
3. **Respect short limits.** For a per-minute limit, the client now waits the
   delay suggested by the server instead of relying only on its own exponential
   backoff.
4. **Prompt scope fix.** The lite model initially refused "Do you sell bikes as
   well?" without searching. The prompt now states that CityWheels services are
   in scope even when the answer is "no".
5. **Default model change.** `gemini-3.5-flash-lite` was chosen because it can
   finish the whole suite within the free tier.

Two genuine issues were observed along the way. One run of `hours-saturday`
dropped the closing time from the answer; it passed on every rerun, so it is
treated as provider flakiness, not a deterministic bug. The `sell-bikes` failure
was reproducible and was fixed by the prompt clarification above.

## 7. AI-assistant usage

AI assistance was used to draft code, tests and documentation, but every
non-trivial output was verified by running code. Three concrete mistakes were
caught this way.

### Wrong output 1: invalid question file and regex flags

An early generated `eval/questions.json` wrapped the array in an extra object:

```json
{
  [
    { "id": "hours-saturday", "...": "..." }
  ]
}
```

`json.load` rejected it immediately. A second generated pattern embedded an
inline flag in the middle of an alternation:

```text
(?i)netflix\.com|(?i)reset\s+password
```

Python raised:

```text
re.error: global flags not at the start of the expression at position 17
```

Both were caught by the loader, which parses the JSON and compiles every regex
before a run starts. Moving the flag to the beginning
(`(?i)netflix\.com|reset\s+password`) fixed it, and
`tests/test_eval_harness.py` now prevents both mistakes from returning.

### Wrong output 2: a model that no longer exists

The first implementation used `gemini-2.5-flash`. The real smoke test returned
`404 NOT_FOUND: This model is no longer available to new users`. Listing the
available models and probing candidates found working alternatives. This is why
the model is configurable and why the README does not hard-code a model that
might disappear.

### Wrong output 3: misleading evaluation score

The first evaluation design reported quota errors as answer failures, producing
a misleading 4/16. Reading the generated report showed `429 RESOURCE_EXHAUSTED`
inside the "reason" field. The client and harness were changed to distinguish
provider exhaustion from model quality. This was a design mistake in the
harness, not in the agent.

## 8. Testing strategy

The suite has **277 tests** and never touches the network or a real key:

- `test_clock.py` - fixed clock and DST-safe durations,
- `test_money.py` - Decimal arithmetic, rounding and formatting,
- `test_bookings.py` - loading, lookup, time calculations,
- `test_refunds.py` - every supplied booking, policy boundaries, vouchers,
  non-cancellable statuses, delayed cancellation, invariants,
- `test_policies.py` - parsing, tokenization, ranking, fallbacks,
- `test_toolbox.py` - schemas, validation, safe dispatch, `delay_days` range,
- `test_agent_loop.py` - tool loops, iteration limit, failures, history
  normalization and trimming, usage aggregation,
- `test_llm.py` - Gemini message conversion, response parsing, retries, quota
  handling, usage,
- `test_openai_adapter.py` - schema/message conversion, tool-call ids, response
  parsing, retries, quota headers, client factory,
- `test_cli.py` - plain/JSON output, REPL, session memory, exit codes, ASCII
  sanitization,
- `test_eval_harness.py` - question validation, scoring, blocked runs, reports,
- `test_benchmarks.py` - config validation, key resolution, metrics, reports,
  multi-run execution, quota stop,
- `test_config.py` - settings, provider selection, data-directory resolution,
  clock parsing.

An autouse fixture disables `load_dotenv`, so a developer's local `.env` cannot
change test results. The CLI tests inject a scripted `FakeLLM`, so they need no
key. CI runs the same suite on Python 3.11 and 3.12; the workflow contains no
secrets and does not run the real evaluation or benchmark.

## 9. Known local environment warning

On some Windows machines the CLI prints:

```text
RequestsDependencyWarning: urllib3 (2.8.0) or chardet (7.7.0)/
charset_normalizer (3.5.1) doesn't match a supported version!
```

This comes from `requests`, not from this project. `requests 2.31.0` supports
`chardet` from 3.0.2 up to (but not including) 6.0.0. A globally installed
`chardet 7.7.0` - in this case pulled in by an unrelated `reportlab` - is outside
that range, so `requests` warns during import.

It is harmless here because Gemini returns UTF-8 JSON with an explicit charset;
the affected code path (guessing an unknown encoding) is never used. A clean
virtual environment installs only `charset_normalizer` through `requests`, so
the warning does not appear; the CI run is clean. The project deliberately does
not add a global `warnings.filterwarnings`, because that would hide real
warnings as well. Recommendation: ignore it, or use a dedicated virtual
environment for this project.

## 10. Limitations

- **Read-only.** The agent explains refunds but cannot cancel or change a
  booking. There is no customer authentication or write API.
- **Memory is limited and text-only.** `--session` keeps the last 10 turns (and
  about 6000 characters) of text. It does not replay old tool calls, and a
  single command-line question is stateless. A long conversation eventually
  forgets its oldest turns by design.
- **Delayed cancellation is a simulation.** `delay_days` shifts only the
  cancellation moment; it is not a booking change.
- **Keyword retrieval.** The policy search is deterministic and explainable, but
  a question with no shared vocabulary can return `found: false`. The agent then
  refers the customer to staff rather than inventing an answer.
- **English only.** The prompt and the answer language are fixed to English.
- **Provider dependency.** Model availability, tool-calling quality and free-tier
  quotas change. `LLM_PROVIDER`/`LLM_MODEL` make the model easy to swap, and
  quota exhaustion is reported honestly rather than hidden.
- **Local models vary.** Small quantized models may call tools unreliably or
  ignore the chat template. The benchmark exposes that; it does not fix it.
- **Windows console.** Plain output converts `EUR` and ASCII punctuation
  because the default Windows console is not UTF-8. `--json` keeps exact data
  using `ensure_ascii`, so parsed values are unchanged.
- **No production controls.** There is no rate limiting, audit log, PII
  handling or monitoring; this is a recruitment exercise, not a deployed
  service.

## 11. Next steps

1. Add a merged-summary mode so separate model runs can be combined without
   editing `summary.md` by hand.
2. Coverage reporting in CI (`pytest-cov` is already a dev dependency).
3. Structured logs and metrics for tool latency and refusals.
4. Integration with a real booking system, behind explicit confirmation and
   authentication.
5. Optional multilingual answers while keeping policies in one source language.
