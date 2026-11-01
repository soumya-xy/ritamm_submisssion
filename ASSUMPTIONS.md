# Assumptions

## Assumption 1: FX rate and date for the EUR mark (Ember Retail GmbH)
- **What I assumed:** EUR/USD = **1.0666**, the ECB euro reference rate published for **Friday 30 December 2022**. 31 December 2022 was a Saturday and the ECB publishes no fixing on weekends, so the last published rate on or before the valuation date applies. EUR 13,100,000 x 1.0666 = USD 13,972,460.00. Cost stays at the Q3 USD figure (USD 14,184,000) and is not re-converted.
- **Source:** ECB, *Euro foreign exchange reference rates, 30 December 2022*, https://www.ecb.europa.eu/stats/exchange/eurofxref/shared/pdf/2022/12/20221230.pdf (stored with the rate in `agent_b/reference/ecb_eurusd.json`).
- **Why:** the Q3 report says CVP converts at "the ECB euro reference rate on the valuation date". For a non-business day, rolling back to the last fixing is the standard convention (the 30 Sep 2022 rate CVP used, 0.9748, is also an ECB fixing on a Friday). The code rolls weekends back automatically (`agent_b/fx.py`) and refuses to guess if the rate is missing from the table.
- **What would change if wrong:** every 0.01 on the rate moves Ember's fair value, the investments total, unrealized gain and NAV by USD 131,000. Using a different convention (e.g. the next business day, 3 Jan 2023) would change the NAV by that amount; the bridge would still balance because it uses the same figure on both sides.

## Assumption 2: Materiality threshold for R1
- **What I assumed:** flag when the fair value moves by **15% or more** versus Q3, **measured in the company's own currency**, not in USD.
- **Why:** this is a one-quarter move, so a band in the 10-25% range is appropriate. Measuring in local currency matters for Ember: its EUR value fell 2.96%, but in USD it "rose" 6.18% only because EUR/USD went from 0.9748 to 1.0666. That is an FX move, not a change in the company's value. The result is not sensitive to the exact choice: the flagged set is identical for any threshold from about 5% to 17.8%, because the next-largest move after the flagged ones is only 4.5% (Aurora), then Harbor at 17.8%. I picked 15% as the round number inside the range. The threshold is configurable (`REVIEW_THRESHOLD_PCT`).
- **What would change if wrong:** at 20% Harbor Analytics (+17.83%) would drop out, leaving Bluetarn (+37.5%) and Cedar (-31.9%). At 10% nothing changes. R1 is a review flag only; it never alters the NAV.

## Assumption 3: Company with no Q4 mark (Ionmere Materials Ltd)
- **What I assumed:** Ionmere was bought on 15 Dec 2022 for USD 14,000,000 and has no CVP mark (CVP's note says its first valuation is Q1 2023). I carry it **at cost, USD 14,000,000.00**, flag it R3 and include it in the NAV.
- **Why:** carrying a recent purchase at cost until its first independent valuation is the common practice and the brief's hint; 16 days between purchase and quarter-end is too short for the price to be stale. Leaving it out would understate NAV by USD 14m and make the bridge fail, since the cash left the bank.
- **What would change if wrong:** the NAV moves one-for-one with any difference between the true value and cost; unrealized gain on this stake is 0 under my assumption.

## Assumption 4: Which stakes the fund holds at 31 December
- **What I assumed:** holdings at 31 Dec = the 8 holdings at 30 Sep + Q4 purchases - Q4 sales (so Fjord Payments out, Ionmere in). The bank statement is the evidence: a USD 29,000,000 inward wire from the buyer on 18 Nov and a USD 14,000,000 outward subscription on 15 Dec. These are the same figures Agent B holds, and Agent B only releases the marks once Agent A's lists match them to the cent.
- **What would change if wrong:** a different holdings set changes investments, realized gain and unrealized change together.

## Assumption 5: Realized gain on Fjord Payments
- **What I assumed:** realized gain = proceeds USD 29,000,000 - Q3 cost USD 20,000,000 = USD 9,000,000. No fees were netted from the proceeds (the wire is the full amount). Its USD 7.5m unrealized gain at 30 Sep reverses through the change in unrealized gain.
- **What would change if wrong:** if there were sale costs, realized gain would be lower, but the bridge would still balance because cash is taken from the bank.

## Assumption 6: Expense classification
- **What I assumed:** following the brief: management fee USD 1,250,000 (paid 3 Oct, labelled Q4), legal fees 38,500 less the 3,500 refund, the D&O premium USD 42,000 (covers 2023 but is **still a Q4 expense** per the brief), and bank charges (850.00) are Q4 expenses. The USD 65,000 administration fee (Jul-Sep) and USD 120,000 audit fee (FY21 final) paid in October settle the two liabilities in the Q3 statement of net assets, so they are `accrual_settlement` and **not** expenses (they are NAV-neutral). The Q4 accruals on sheet 2 (USD 130,000 + 65,000 = 195,000) are expenses in full and are the Q4 liabilities.
- **What would change if wrong:** misclassifying a settlement as an expense would overstate expenses by USD 185,000 and break the bridge by exactly that amount (this is covered by a test).

## Assumption 7: Investor behind the "R MEHTA FAM TRST" wire
- **What I assumed:** the 9 Nov USD 800,000 wire has no LP code in its text, only a garbled name ("R MEHTA FAM TRST ... CALL NO 7"). I map it to **LP10, R. Mehta Family Trust** from the investor roster in the Q3 NAV summary, and as a capital call (the text says "CALL NO 7", the same call as the others).
- **What would change if wrong:** the amount is in capital called either way; only the LP attribution would change.

## Assumption 8: Counting and file conventions
- **What I assumed:** `transactions.json` holds the 39 real movements; the "balance brought forward" line is excluded (it is recorded as the opening balance in `reconciliation.json`). Amounts are exact decimals written to 2 places. `review.json` has one entry per company that appears anywhere (including Fjord, sold, and Ionmere, unmarked).
