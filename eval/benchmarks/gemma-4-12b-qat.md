# Benchmark: gemma-4-12b-qat

- **Provider:** `openai`
- **Model:** `google/gemma-4-12b-qat`
- **Base URL:** `http://localhost:1234/v1`
- **Note:** Lokalny model Gemma 4 12B w LM Studio.
- **Runs:** 1/1 completed
- **Questions per run:** 16
- **Pass rate:** 11/16 (68.8%)
- **Tool accuracy:** 100.0%
- **Mean latency:** 32.47s
- **p95 latency:** 67.69s
- **Completion tokens:** 3208
- **Tokens/s:** 6.2
- **Errors:** 0
- **Blocked by quota:** 0

## Per-run summary

| Run | Passed | Failed | Blocked | Mean latency |
| --- | --- | --- | --- | --- |
| 1 | 11 | 5 | 0 | 32.5s |

## Per-question results

| Run | Question | Result | Tools | Time |
| --- | --- | --- | --- | --- |
| 1 | `hours-saturday` | PASS | `search_policies` | 22.7s |
| 1 | `price-ebike-day` | PASS | `search_policies` | 15.8s |
| 1 | `age-14-ebike` | PASS | `search_policies` | 23.9s |
| 1 | `child-seat-price` | PASS | `search_policies` | 13.2s |
| 1 | `deposit-city` | PASS | `search_policies` | 17.4s |
| 1 | `bike-abroad` | PASS | `search_policies` | 18.8s |
| 1 | `sell-bikes` | PASS | `search_policies` | 14.2s |
| 1 | `refund-1002` | FAIL | `get_booking`, `calculate_refund` | 62.9s |
| 1 | `refund-1001` | FAIL | `get_booking`, `calculate_refund` | 51.1s |
| 1 | `refund-1003` | FAIL | `get_booking`, `calculate_refund` | 46.5s |
| 1 | `refund-1007` | FAIL | `get_booking`, `calculate_refund` | 59.3s |
| 1 | `refund-1010` | FAIL | `get_booking`, `calculate_refund` | 67.7s |
| 1 | `refund-1004-voucher` | PASS | `get_booking`, `calculate_refund` | 52.5s |
| 1 | `refusal-australia` | PASS | - | 15.1s |
| 1 | `refusal-netflix` | PASS | - | 18.5s |
| 1 | `unknown-booking` | PASS | `get_booking` | 20.0s |
