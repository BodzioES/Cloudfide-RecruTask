# Benchmark: gemma-4-26b-a4b-qat

- **Provider:** `openai`
- **Model:** `google/gemma-4-26b-a4b-qat`
- **Base URL:** `http://localhost:1234/v1`
- **Note:** Local Gemma 4 26B (A4B MoE) model served by LM Studio.
- **Runs:** 1/1 completed
- **Questions per run:** 16
- **Pass rate:** 10/16 (62.5%)
- **Tool accuracy:** 100.0%
- **Mean latency:** 9.34s
- **p95 latency:** 31.17s
- **Completion tokens:** 5675
- **Tokens/s:** 38.0
- **Errors:** 0
- **Blocked by quota:** 0

## Per-run summary

| Run | Passed | Failed | Blocked | Mean latency |
| --- | --- | --- | --- | --- |
| 1 | 10 | 6 | 0 | 9.3s |

## Per-question results

| Run | Question | Result | Tools | Time |
| --- | --- | --- | --- | --- |
| 1 | `hours-saturday` | PASS | `search_policies` | 15.6s |
| 1 | `price-ebike-day` | PASS | `search_policies` | 4.2s |
| 1 | `age-14-ebike` | FAIL | `search_policies` | 6.9s |
| 1 | `child-seat-price` | PASS | `search_policies` | 5.1s |
| 1 | `deposit-city` | PASS | `search_policies` | 6.6s |
| 1 | `bike-abroad` | PASS | `search_policies` | 5.4s |
| 1 | `sell-bikes` | PASS | `search_policies` | 4.7s |
| 1 | `refund-1002` | FAIL | `calculate_refund` | 7.6s |
| 1 | `refund-1001` | FAIL | `get_booking`, `calculate_refund` | 15.0s |
| 1 | `refund-1003` | FAIL | `calculate_refund` | 8.2s |
| 1 | `refund-1007` | FAIL | `calculate_refund` | 8.0s |
| 1 | `refund-1010` | FAIL | `get_booking`, `calculate_refund` | 31.2s |
| 1 | `refund-1004-voucher` | PASS | `get_booking`, `calculate_refund` | 18.6s |
| 1 | `refusal-australia` | PASS | - | 3.0s |
| 1 | `refusal-netflix` | PASS | - | 4.6s |
| 1 | `unknown-booking` | PASS | `get_booking` | 4.7s |
