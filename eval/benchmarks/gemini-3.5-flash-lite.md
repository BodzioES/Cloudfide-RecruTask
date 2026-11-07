# Benchmark: gemini-3.5-flash-lite

- **Provider:** `gemini`
- **Model:** `gemini-3.5-flash-lite`
- **Note:** Bazowy model API (Google AI Studio).
- **Runs:** 1/1 completed
- **Questions per run:** 16
- **Pass rate:** 15/16 (93.8%)
- **Tool accuracy:** 100.0%
- **Mean latency:** 7.44s
- **p95 latency:** 41.68s
- **Completion tokens:** 1719
- **Tokens/s:** 14.4
- **Errors:** 0
- **Blocked by quota:** 0

## Per-run summary

| Run | Passed | Failed | Blocked | Mean latency |
| --- | --- | --- | --- | --- |
| 1 | 15 | 1 | 0 | 7.4s |

## Per-question results

| Run | Question | Result | Tools | Time |
| --- | --- | --- | --- | --- |
| 1 | `hours-saturday` | PASS | `search_policies` | 16.6s |
| 1 | `price-ebike-day` | PASS | `search_policies` | 3.2s |
| 1 | `age-14-ebike` | PASS | `search_policies` | 1.4s |
| 1 | `child-seat-price` | PASS | `search_policies` | 1.3s |
| 1 | `deposit-city` | PASS | `search_policies` | 1.2s |
| 1 | `bike-abroad` | PASS | `search_policies` | 1.1s |
| 1 | `sell-bikes` | PASS | `search_policies` | 1.2s |
| 1 | `refund-1002` | PASS | `get_booking`, `calculate_refund` | 41.7s |
| 1 | `refund-1001` | PASS | `get_booking`, `calculate_refund` | 3.0s |
| 1 | `refund-1003` | PASS | `get_booking`, `calculate_refund` | 2.8s |
| 1 | `refund-1007` | PASS | `get_booking`, `calculate_refund` | 4.6s |
| 1 | `refund-1010` | FAIL | `get_booking`, `calculate_refund` | 1.9s |
| 1 | `refund-1004-voucher` | PASS | `get_booking`, `calculate_refund` | 2.4s |
| 1 | `refusal-australia` | PASS | - | 1.1s |
| 1 | `refusal-netflix` | PASS | - | 5.8s |
| 1 | `unknown-booking` | PASS | `get_booking` | 29.7s |
