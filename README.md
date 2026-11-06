# CityWheels help desk agent

[![tests](https://github.com/BodzioES/Cloudfide-RecruTask/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/BodzioES/Cloudfide-RecruTask/actions/workflows/tests.yml)

A command-line AI assistant for the fictional **CityWheels** bike rental shop.
It answers questions about shop rules, looks up bookings, calculates refunds in
code, and refuses topics unrelated to the rental service.

The language model never computes money and never invents booking data: every
fact comes from a tool result. The project ships with an offline test suite and
a reproducible evaluation harness.

## What it can do

- answer policy questions (opening hours, prices, deposits, age limits, refund
  rules) from the Markdown documents in `data/policies/`,
- look up a booking by its number (`CW-1001`, `CW-1002`, ...),
- calculate a cancellation refund with an exact breakdown (rental, deposit,
  booking fee) and the refund method (card or voucher),
- say "not found" for unknown bookings instead of guessing,
- refuse out-of-scope questions and point the customer to the staff,
- run as a single question, an interactive session, or JSON output.

## Requirements

- Python **3.11** or newer,
- a Google AI Studio API key ([create one here](https://aistudio.google.com/apikey)),
- internet access for the model calls (tests and CI do not need it).

## Quickstart

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

python -m pip install -e ".[dev]"

# Windows
copy .env.example .env
# macOS / Linux
cp .env.example .env
```

Open `.env` and set your key:

```dotenv
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-3.5-flash-lite
```

`.env` is ignored by Git and must never be committed. If you prefer environment
variables, set `GEMINI_API_KEY` directly instead of using a file.

## Usage

Ask one question:

```bash
python -m citywheels "What time do you open on Saturday?"
```

Start the interactive session (finish with `exit`, `quit` or Ctrl+D):

```bash
python -m citywheels
```

Other options:

```bash
# machine-readable result: answer, tool calls, iterations, error
python -m citywheels "I want to cancel booking CW-1002. How much will I get back?" --json

# show tool calls and debug details on stderr
python -m citywheels "What deposit do I pay for a city bike?" --verbose

# override the fixed reference time
python -m citywheels "What time do you open on Saturday?" --now 2026-12-01T09:00:00+01:00

# console entry point, installed by pip
citywheels "Can I take a rented bike to another country?"
```

Exit codes: `0` success, `1` runtime failure (model, tool or iteration limit),
`2` usage or configuration error (missing key, bad data, empty question).

## Example transcript

```text
$ python -m citywheels "What time do you open on Saturday?"
On Saturdays, we are open from 08:00 to 20:00 from April to October, and from
09:00 to 17:00 from November to March.

$ python -m citywheels "I want to cancel booking CW-1002. How much will I get back?"
If you cancel booking CW-1002 right now, you will get back a total of 340.00 EUR.

Here is the breakdown:
* Rental refund: 40.00 EUR (50% of the 80.00 EUR rental price, since it is
  cancelled 33 hours before the start)
* Deposit refund: 300.00 EUR (refunded in full)
* Booking fee: 0.00 EUR (non-refundable)

The total refund of 340.00 EUR will be returned to your original card within
5 working days. Please note that I can only provide this information, and our
staff will need to perform the actual cancellation for you.

$ python -m citywheels "What is the capital of Australia?"
I can only help with CityWheels rentals. If you need assistance, please contact
our staff at help@citywheels.example or +48 000 000 000.
```

## Tests

The suite is fully offline: no API key, no network, no real `.env` file.

```bash
python -m pytest -q
```

**200 tests** cover the clock and DST handling, money arithmetic, booking and
refund rules, policy search, tool validation, the agent loop, the Gemini adapter,
the CLI and the evaluation harness. CI runs the same command on Python 3.11 and
3.12 (see `.github/workflows/tests.yml`).

## Evaluation

The harness sends 16 real questions to Gemini, checks tools and answer patterns
locally, and writes a Markdown report.

```bash
python eval/run_eval.py                      # full suite
python eval/run_eval.py --limit 5            # first five questions
python eval/run_eval.py --only refund-1002   # one question (repeatable)
python eval/run_eval.py --model gemini-3.6-flash
```

Results are written to `eval/results.md`. Exit codes: `0` all passed, `1` some
question failed, `2` invalid configuration or question file, `3` part of the run
was blocked by the provider quota (remaining questions are marked `BLOCKED`, not
reported as model failures).

The committed report is **16/16 passed** on `gemini-3.5-flash-lite`. See
[`SOLUTION.md`](SOLUTION.md) for the failures found on the way and how they were
fixed.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `GEMINI_API_KEY` | API key (required for real runs) | none |
| `GEMINI_MODEL` | Gemini model name | `gemini-3.5-flash-lite` |
| `CITYWHEELS_DATA_DIR` | override the data directory | auto-detected `data/` |
| `CITYWHEELS_NOW` | fixed reference time | `2026-06-10T09:00:00+02:00` |

The data directory is found by walking up from the package and the current
working directory, so the CLI works from the repository root and from installed
packages.

## Project layout

```text
src/citywheels/       application code
  cli.py              argparse CLI, REPL, JSON and plain output
  config.py           settings, data-directory lookup, fixed clock
  agent.py            model -> tool calls -> model loop
  llm.py              Gemini adapter, retries, response parsing, FakeLLM
  prompts.py          system prompt with grounding rules
  bookings.py         booking model and JSON store
  policies.py         deterministic Markdown policy search
  refunds.py          cancellation rules and refund breakdown
  money.py            Decimal helpers and EUR formatting
  clock.py            timezone-safe duration calculations
  tools/              tool schemas, validation and safe dispatcher
tests/                offline pytest suite (200 tests)
eval/                 question set, runner and committed results
data/                 policy documents and example bookings
.github/workflows/    CI: pytest on Python 3.11 and 3.12
```

## Data

`data/bookings.json` contains example bookings, and `data/policies/*.md` contains
the shop rules. The reference time is fixed at **2026-06-10 09:00 Europe/Warsaw
(UTC+02:00)** so answers are reproducible; use `--now` to test other moments.

## Notes

- The default model is a free-tier-friendly Gemini model. Free-tier quotas vary
  by model: the full-size flash models allow only 20 requests per day, so the
  complete evaluation may need a different model or a paid plan. The harness
  stops early instead of misreporting quota errors.
- On some Windows setups, `requests` prints a `RequestsDependencyWarning` caused
  by an unrelated global `chardet` version. It is harmless and absent in CI;
  details are in [`SOLUTION.md`](SOLUTION.md#9-known-local-environment-warning).
- The agent is read-only: it explains what would happen but cannot cancel or
  change a booking. Staff performs the actual change.
