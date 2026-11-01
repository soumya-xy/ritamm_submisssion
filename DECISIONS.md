# Decisions

## Decision 1: Don't trust the LLM's output - validate it in code and cross-check it
**Decision:** The LLM classifies every transaction, but each answer is checked before it is accepted: the category must be one of the 11 allowed; capital_call / distribution must have an LP code that exists in the roster; purchase / sale must name a company; and the direction must fit (a capital call cannot be a debit). A rejected answer is sent back once with the exact reason; if it is still wrong that single row falls back to keyword rules and is labelled `rules_fallback`. Independently, every accepted answer is compared with the keyword rules and any disagreement is recorded. A hard LLM failure (bad key, wrong model) stops the run instead of silently using rules for everything.
**Alternatives:** (a) accept the model's answer as-is; (b) use only keyword rules and skip the LLM (the brief requires an LLM); (c) have the LLM do everything including the arithmetic.
**Why:** Money is on the line and models occasionally get a row wrong. The checks are cheap, deterministic and catch the errors that would matter (wrong direction, invented LP code). The bridge is a second line of defence: a wrong category produces a non-zero difference, so the run fails loudly instead of printing a wrong NAV.

## Decision 2: Parse the PDF by word coordinates, and prove the result with arithmetic
**Decision:** I read each word's x/y position with pdfplumber, start a transaction on each dated line, append undated lines as wrapped description, and assign amounts to Debit/Credit/Balance by the right edge of the number. Then `reconcile()` checks every running balance, opening + movements = closing, and the totals printed on the statement.
**Alternatives:** regex over the flattened text; LLM-based extraction; a table extractor.
**Why:** flat text loses which column a number was in and mangles wrapped descriptions. The reconciliation paid off during development: my first version lost the USD 14,000,000 debit because a wide number started inside the reference column, and the balance check failed immediately instead of producing a silently wrong NAV. I fixed it by classifying amounts by their right edge and added a regression test.

## Decision 3: Exact decimals end to end, and a bridge computed independently of the NAV
**Decision:** All money is `Decimal` (never float), read and written with simplejson so values like 8150.27 survive exactly. NAV is computed from the position side (closing cash + investments - liabilities), and the bridge from the flow side (capital called, distributions, income, expenses, gains). `difference` is the gap between them. I also added a separate cash proof (Q3 cash + every categorized movement = closing cash).
**Alternatives:** floats; computing NAV as Q3 NAV + flows (which can never disagree with itself); using the A2A SDK and pydantic models.
**Why:** floats can leave 1-cent errors that make "to the cent" unprovable. Two independent computations mean the difference is a real test of the categorization, not a tautology. Plain dict-based JSON-RPC keeps the wire format readable and means I can explain every line of the A2A layer.
