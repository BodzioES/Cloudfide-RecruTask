# Solution notes

This document explains how the CityWheels help desk agent is built, why the
important decisions were made, what the evaluation found, and what remains
limited. For installation and usage, see [`README.md`](README.md).

## 1. What was built

A Python 3.11+ command-line assistant that:

- answers shop-policy questions from `data/policies/*.md`,
- looks up bookings from `data/bookings.json`,
- computes cancellation refunds in code,
- refuses unrelated questions,
- never performs a cancellation or booking change itself.

The model is Google **Gemini** through the `google-genai` SDK. The model chooses
tools and writes the explanation; all numbers, booking data and policy text come
from deterministic code and local files.

## 2. Install, run, test, evaluate

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"

cp .env.example .env          # Windows: copy .env.example .env
# put GEMINI_API_KEY=... in .env

python -m citywheels "What time do you open on Saturday?"
python -m citywheels                                    # interactive REPL
python -m citywheels "..." --json                       # full result as JSON
python -m citywheels "..." --verbose                    # tool calls on stderr

python -m pytest -q                                     # 200 offline tests
python eval/run_eval.py                                 # real Gemini evaluation
```

Useful evaluation flags: `--limit N`, `--only ID` (repeatable or comma-separated),
`--model NAME`, `--now ISO8601`, `--delay SECONDS`, `--output PATH`.

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
`GEMINI_MODEL`; `gemini-3.6-flash` gives good answers for individual questions
but cannot complete the full evaluation on a free key. `temperature` is `0` so
repeated runs are as stable as the provider allows.

No embeddings are used. The policy corpus is three short Markdown files, so a
local keyword scorer is faster, deterministic and easier to test.

## 4. Architecture

```text
CLI (cli.py)
  ├─ Settings (config.py)            env vars, data directory, fixed clock
  ├─ Toolbox (tools/)               schemas, validation, safe dispatcher
  │    ├─ BookingStore              data/bookings.json
  │    ├─ PolicyIndex               data/policies/*.md
  │    └─ fixed "now"               2026-06-10T09:00:00+02:00
  └─ run_agent (agent.py)
       └─ GeminiClient (llm.py) ── function calls ──> Toolbox.dispatch
```

The loop in `agent.py` is deliberately small:

1. send the question and the three tool schemas to the model,
2. if the model requests tools, dispatch each one and append the results,
3. repeat until the model returns text or six turns have passed,
4. return an `AgentResult` with the answer, the executed calls, the iteration
   count and an optional error.

Model and tool failures are captured in the result. They never crash the CLI:
the customer sees a short apology and the staff contact details.

### Tool contracts

| Tool | Input | Output |
| --- | --- | --- |
| `search_policies` | `query`, optional `top_k` (1-10) | ranked sections with source, heading, text and score |
| `get_booking` | `booking_id` | booking details, or `found: false` with a hint |
| `calculate_refund` | `booking_id` | percent, rental/deposit/fee refunds, total, method, reason |

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
Scoring is local and deterministic; only the answers come from Gemini. The
run writes `eval/results.md` with the score, tool calls, timings and full
answers.

### Final result

**16/16 passed** on `gemini-3.5-flash-lite` (committed in `eval/results.md`).

The answers contain the expected amounts and explanations, for example
`340.00 EUR` for CW-1002 with a 50% rental refund and the card refund method, and
`50.00 EUR` for CW-1003 where the rental refund is 0% but the deposit is
returned.

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

The suite has **200 tests** and never touches the network or a real key:

- `test_clock.py` - fixed clock and DST-safe durations,
- `test_money.py` - Decimal arithmetic, rounding and formatting,
- `test_bookings.py` - loading, lookup, time calculations,
- `test_refunds.py` - every supplied booking, policy boundaries, vouchers,
  non-cancellable statuses, invariants,
- `test_policies.py` - parsing, tokenization, ranking, fallbacks,
- `test_toolbox.py` - schemas, validation, safe dispatch,
- `test_agent_loop.py` - tool loops, iteration limit, model and tool failures,
- `test_llm.py` - message conversion, response parsing, retries, quota handling,
- `test_cli.py` - plain/JSON output, REPL, exit codes, ASCII sanitization,
- `test_eval_harness.py` - question validation, scoring, blocked runs, reports,
- `test_config.py` - settings, data-directory resolution, clock parsing.

An autouse fixture disables `load_dotenv`, so a developer's local `.env` cannot
change test results. The CLI tests inject a scripted `FakeLLM`, so they need no
key. CI runs the same suite on Python 3.11 and 3.12; the workflow contains no
secrets and does not run the real evaluation.

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
- **Single-turn evaluation.** The REPL answers one question at a time; it does
  not keep conversation memory for follow-ups such as "and if I cancel
  tomorrow?".
- **Keyword retrieval.** The policy search is deterministic and explainable, but
  a question with no shared vocabulary can return `found: false`. The agent then
  refers the customer to staff rather than inventing an answer.
- **English only.** The prompt and the answer language are fixed to English.
- **Provider dependency.** Model availability and free-tier quotas change.
  `GEMINI_MODEL` makes the model easy to swap, and quota exhaustion is reported
  honestly rather than hidden.
- **Windows console.** Plain output converts `EUR` and ASCII punctuation
  because the default Windows console is not UTF-8. `--json` keeps exact data
  using `ensure_ascii`, so parsed values are unchanged.
- **No production controls.** There is no rate limiting, audit log, PII
  handling or monitoring; this is a recruitment exercise, not a deployed
  service.

## 11. Next steps

1. Conversation memory for follow-up questions in the REPL.
2. A second model in the evaluation, with a small comparison table.
3. Coverage reporting in CI (`pytest-cov` is already a dev dependency).
4. Structured logs and metrics for tool latency and refusals.
5. Integration with a real booking system, behind explicit confirmation and
   authentication.
6. Optional multilingual answers while keeping policies in one source language.
