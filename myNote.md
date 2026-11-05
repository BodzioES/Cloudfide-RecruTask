# Working notes

Personal scratchpad for the CityWheels assignment: open questions, decisions
that need a second look, and things to verify before the final commit.

This file is not part of the deliverable. Fold anything useful into
`SOLUTION.md` (or delete this file) before sending the repository link.

---

## 1. Rounding policy — decided, but worth revisiting

Current choice: `ROUND_HALF_UP`, applied **once**, to the final result
(`money.quantize`). All amounts are `Decimal`; `bookings.json` is parsed with
`parse_float=Decimal`, so no `float` ever touches the maths.

Open points:

- **Who wins on a tie?** For a **refund** (money leaving the shop), half-up
  gives the customer at most half a cent. For a **charge** (late fee, damage),
  the same rule gives the shop at most half a cent. A single global constant is
  therefore only safe while we compute refunds; a future charge must pick its
  own policy explicitly.
- **Alternatives:** `ROUND_HALF_EVEN` (unbiased over many ties, standard in
  accounting) and truncation (always underpays the customer — rejected).
- **Does it matter today?** No. Prices are whole euros and refund percentages
  are 0/50/100, so rounding never changes a value. The risk appears only if
  prices or percentages gain cents.
- **Safeguards already in place:** exact `Decimal` arithmetic, single rounding
  at the end, explicit `ROUNDING` constant, tie tests
  (`50% of 12.35 -> 6.18`, `50% of 12.34 -> 6.17`), no-float regression test.
- **Safeguards to add in stage 2:** invariants — parts sum to the total,
  `total <= rental_price + deposit`, booking fee refund is always zero,
  non-cancellable bookings refund nothing.
- **To document in `SOLUTION.md`:** a short "Rounding policy" section stating
  the rule, who it favours, and when it would start to matter.
- **Open question for the reviewer:** should refunds round *up* (ceiling)
  instead of half-up to be unambiguously customer-friendly? It is a one-line
  change if the answer is yes.

## 2. Refund semantics — our reading of the documents

- "How much will I get back" = rental refund + deposit. The booking fee is
  never refunded.
- The deposit is always refunded in full for a `confirmed` booking, including
  the `< 24 h` case where the rental refund is 0%.
- Statuses `in_progress`, `completed`, `cancelled`: nothing is refunded —
  including the deposit. This is our reading of "nothing is refunded for them".
  **Verify** this interpretation if the reviewer disagrees.
- Voucher bookings: refunded as a new voucher, never as money; the deposit
  follows the original payment method.
- Boundaries are inclusive: `>= 48 h` -> 100%, `>= 24 h and < 48 h` -> 50%,
  `< 24 h` -> 0%. Compare exact `timedelta`, never whole hours.
- A `confirmed` booking whose start is already in the past still follows the
  `< 24 h` rule (0% + deposit). Not present in the data, covered by a test.

## 3. Data quirks to remember

- `rental_price_eur` is stored in the data — use it for refunds, do not
  recompute it. Optional sanity check against `prices.md`: CW-1009 is 7 days
  with the 15% discount (140 -> 119).
- CW-1010 and CW-1006 have `booking_fee_eur = 0` (booked in the shop).
- Only CW-1004 was paid with a voucher.
- All delivered bookings are in June. DST behaviour is covered by a synthetic
  October test, not by real data.

## 4. Policy retrieval

- Deterministic keyword scoring over Markdown headings; no embeddings, no
  network.
- Risk: paraphrases ("how late are you open", "cost of an electric bike") may
  miss the right chunk. Test against every example question from the README.
- Cap the size of chunks returned to the model; `top_k = 3` should be enough.
- Return `source` and `heading` so the agent can cite where an answer came
  from.

## 5. Model behaviour to verify

- The model must call `calculate_refund` instead of doing mental arithmetic.
  Assert tool calls in the evaluation; consider forcing tool choice for refund
  questions if Gemini is unreliable.
- Answer language: the project decision is **English only**. Verify the prompt
  does not silently mirror a Polish question.
- Out-of-scope questions (capital of Australia, Netflix password) must be
  refused and point to `help@citywheels.example` / `+48 000 000 000`.
- Bad weather, damage and theft are staff decisions — the agent must hand off,
  not decide.
- Unknown booking number: say "not found", never invent details.

## 6. Operations

- Verify `gemini-2.5-flash` is available with the actual API key; keep a
  fallback model in mind.
- Free tier has rate limits: retry with backoff, and keep the eval run small.
- CI runs only fake-model tests (no key). The evaluation is run manually.
- Windows `core.autocrlf=true` produces LF/CRLF warnings; consider adding
  `.gitattributes` with `* text=auto eol=lf`.
- Decide whether this `myNote.md` stays in the repository before submission.

## 7. Evaluation design

- At least 10 questions, at least 2 refunds, at least 2 refusals, plus an
  unknown booking number.
- Assertions: expected tool subset, regex on amounts (accept `340.00`),
  refusal detected via the contact details, no invented numbers.
- Report failures honestly in `eval/results.md`, with a one-line reason each.
- Keep the questions deterministic; `temperature = 0`.
