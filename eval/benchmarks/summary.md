# Benchmark summary

- **Generated:** `2026-11-07T15:41:34+00:00`
- **Scoring:** the same offline checks as `eval/run_eval.py`.
- **Source:** the per-model JSON files in this directory; each row is the latest recorded run for that model.

| Model | Provider | Runs | Pass rate | Tool accuracy | Mean | p95 | Tokens/s | Blocked | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gemini-3.5-flash-lite` | `gemini` | 1/1 | 15/16 (93.8%) | 100.0% | 7.44s | 41.68s | 14.4 | 0 | Baseline API model (Google AI Studio). |
| `gemma-4-12b-qat` | `openai` | 1/1 | 11/16 (68.8%) | 100.0% | 32.47s | 67.69s | 6.2 | 0 | Local Gemma 4 12B model served by LM Studio. |
| `gemma-4-26b-a4b-qat` | `openai` | 1/1 | 10/16 (62.5%) | 100.0% | 9.34s | 31.17s | 38.0 | 0 | Local Gemma 4 26B (A4B MoE) model served by LM Studio. |

Raw answers and tool calls are in the per-model JSON files.
