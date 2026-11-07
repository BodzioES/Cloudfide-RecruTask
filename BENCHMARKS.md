# Model benchmarks

This document explains how to compare several models on the same CityWheels
task, how the runner measures them, and how to read the results. It is a manual
procedure: the benchmark calls real providers, so it is not part of CI.

## What is measured

Every model gets exactly the same input:

- the same `eval/questions.json` (16 questions: policies, refunds, refusals,
  unknown booking),
- the same system prompt from `src/citywheels/prompts.py`,
- the same fixed reference time `2026-06-10T09:00:00+02:00`,
- the same `temperature = 0`,
- the same local scoring rules as `eval/run_eval.py`.

The runner reports:

| Metric | Meaning |
| --- | --- |
| Pass rate | Questions whose tools and answer patterns match the expected result. |
| Tool accuracy | Share of questions where the model called every expected tool. |
| Mean latency | Average wall-clock time per question. |
| p95 latency | Slowest 5% boundary; exposes the occasional very slow answer. |
| Tokens/s | Completion tokens divided by total question time, when the provider reports token usage. |
| Errors | Model/tool/iteration errors inside executed questions. |
| Blocked | Questions not executed because the provider quota was exhausted. |

## Suggested model set

A practical set:

1. `gemini-3.5-flash-lite` - the committed baseline (free API).
2. a second free API model through an OpenAI-compatible endpoint, for example
   an OpenRouter free model or a Groq model.
3. one local LM Studio model around 7B-8B, for example Qwen3 8B.
4. a second local model around 8B-14B, for example Llama 3.1 8B Instruct or
   Qwen3 14B in Q4.

The local models must support tool/function calling. A model that only writes
JSON as plain text will fail the tool assertions, which is a real result, not a
runner bug.

## 1. Configure the providers

Copy the example config and edit it:

```bash
cp eval/benchmark_models.json eval/benchmark_models.local.json
```

`eval/benchmark_models.json` is committed and contains four example entries.
Keep API keys out of it: use `api_key_env` and put the key in `.env` (or the
shell environment).

```json
{
  "questions": "questions.json",
  "reference_now": "2026-06-10T09:00:00+02:00",
  "runs": 3,
  "models": [
    {
      "label": "gemini-3.5-flash-lite",
      "provider": "gemini",
      "model": "gemini-3.5-flash-lite",
      "api_key_env": "GEMINI_API_KEY"
    },
    {
      "label": "openrouter-free",
      "provider": "openai-compatible",
      "model": "meta-llama/llama-3.3-70b-instruct:free",
      "base_url": "https://openrouter.ai/api/v1",
      "api_key_env": "OPENROUTER_API_KEY"
    },
    {
      "label": "lmstudio-qwen3-8b",
      "provider": "openai-compatible",
      "model": "qwen3-8b",
      "base_url": "http://localhost:1234/v1",
      "api_key": "lm-studio"
    },
    {
      "label": "lmstudio-llama-3.1-8b",
      "provider": "openai-compatible",
      "model": "llama-3.1-8b-instruct",
      "base_url": "http://localhost:1234/v1",
      "api_key": "lm-studio"
    }
  ]
}
```

`provider` accepts `gemini`, `openai`, `openai-compatible`, `local` and
`google`; they are normalized to the two concrete adapters. A missing key skips
that model with a message instead of aborting the whole benchmark.

## 2. Check one model before the full run

Use the normal CLI or the evaluation runner first:

```bash
python -m citywheels "What time do you open on Saturday?" --verbose
python eval/run_eval.py --limit 2 --model qwen3-8b \
  --provider openai-compatible --base-url http://localhost:1234/v1
```

If the model cannot call tools, fix the model choice or its chat template before
spending time on a full benchmark.

## 3. Run the benchmark

```bash
# all configured models, 3 runs each
python eval/benchmark.py --config eval/benchmark_models.local.json

# only one model, quicker smoke test
python eval/benchmark.py --model lmstudio-qwen3-8b --limit 4 --runs 1

# full run without waiting between questions (local servers)
python eval/benchmark.py --config eval/benchmark_models.local.json --delay 0
```

Three runs are the default because local models are less stable than hosted
ones. Do not compare a 1-run result with a 3-run result.

The runner writes:

```text
eval/benchmarks/
  summary.md                 comparison table
  gemini-3.5-flash-lite.md   readable per-model report
  gemini-3.5-flash-lite.json raw answers, tools, timings and usage
  lmstudio-qwen3-8b.md
  lmstudio-qwen3-8b.json
  ...
```

Exit codes: `0` all executed questions passed, `1` at least one failure,
`2` configuration problem or no model could run, `3` a provider quota blocked
part of the run.

## 4. LM Studio setup

1. Install LM Studio and download a model with tool-use support.
2. Load the model with enough context (8192 tokens or more). The system prompt
   plus policy chunks plus history needs room.
3. Start the local server (Developer tab). The default endpoint is
   `http://localhost:1234/v1`.
4. Use any non-empty API key; LM Studio ignores it. The examples use
   `lm-studio`.
5. Verify the exact model id:

```bash
curl http://localhost:1234/v1/models
```

Use that id as `model` in the benchmark config.

## 5. Reading the results honestly

- **Pass rate alone is not enough.** A model can pass by guessing the right tool
  but produce a wrong amount; the answer-pattern checks catch that.
- **Tool accuracy matters.** If it is much lower than pass rate, the model may
  be answering from memory instead of calling tools.
- **Latency on the first question includes model loading.** Exclude the first
  question when comparing mean latency.
- **Small sample.** 16 questions are useful for a smoke comparison, not for a
  scientific ranking. Three runs help, but do not over-interpret one failure.
- **Quota blocking is not a model failure.** A blocked run means the provider
  refused to answer; the summary marks it separately.
- **Same conditions.** Do not change the prompt, the question set or `--now`
  between models and then compare the tables.

## 6. Suggested report table

After the run, copy the summary numbers into something like this:

| Model | Provider | Runs | Pass rate | Tool accuracy | Mean | p95 | Tokens/s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gemini-3.5-flash-lite` | Gemini API | 3 | 100% | 100% | ... | ... | ... | baseline |
| second API model | OpenAI-compatible | 3 | ... | ... | ... | ... | ... | ... |
| local 8B | LM Studio | 3 | ... | ... | ... | ... | ... | ... |
| local 14B | LM Studio | 3 | ... | ... | ... | ... | ... | ... |

The committed baseline is **16/16** on `gemini-3.5-flash-lite`
(`eval/results.md`). Use it as the reference point, not as proof that a larger
model must be better on this narrow task.

The committed comparison in `eval/benchmarks/summary.md` covers the baseline
and two local Gemma models. If you run models one at a time, do not edit the
summary by hand: rerun all models in one invocation so the table always matches
the per-model JSON files.
