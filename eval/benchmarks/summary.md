# Benchmark summary

- **Generated:** `2026-11-07T15:41:34+00:00`
- **Scoring:** the same offline checks as `eval/run_eval.py`.

| Model | Provider | Runs | Pass rate | Tool accuracy | Mean | p95 | Tokens/s | Blocked | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gemini-3.5-flash-lite` | `gemini` | 1/1 | 16/16 (100.0%) | 100.0% | 6.80s | 41.70s | n/a | 0 | Bazowy model API (Google AI Studio). |
| `gemma-4-12b-qat` | `openai` | 1/1 | 11/16 (68.8%) | 100.0% | 32.47s | 67.69s | 6.2 | 0 | Lokalny model Gemma 4 12B w LM Studio. |

Raw answers and tool calls are in the per-modelJSON files.
VRAM and cold-start time are hardware facts, not API facts: record them manually next to the table if the benchmark is used for a report.
