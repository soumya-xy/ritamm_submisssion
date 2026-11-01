# GPF Q4 2022 close - two A2A agents

Closes the quarter ended 31 Dec 2022 for *The Greatness PE Fund, L.P.*: reads the bank statement, categorizes
every transaction with an LLM, obtains CVP's Q4 marks from a second agent over A2A, reviews the marks and
computes the NAV and a NAV bridge that balances to the cent.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
python main.py ./pack       # results appear in ./output
pytest                      # offline: no LLM, no internet
```

* `python main.py [PACK_DIR] [--output DIR]` - PACK_DIR defaults to `./pack` (or `$PACK_DIR`), output to `./output`.
* Environment (a real environment variable beats `.env`): `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` (any
  OpenAI-compatible endpoint), `AGENT_A_PORT` (8001), `AGENT_B_PORT` (8002).
  Optional: `REVIEW_THRESHOLD_PCT` (15), `LLM_BATCH_SIZE` (8), `FUND_CODE` (GPF), `VALUATION_DATE` (2022-12-31).
* Exit code 0 on success; 2 for missing configuration; 1 if anything fails (including a bridge that does not balance).
  Both agents are shut down when the run ends.

## What it does

```
main.py ──A2A──► Agent A (Bank & Ingestion) ──A2A (2 turns)──► Agent B (Valuation & NAV)
                  parse PDF, prove arithmetic,                    holds CVP marks + Q3 files
                  LLM categorizes, derive buys/sells              checks A's buys/sells, releases marks
main.py ──► Agent B's review + NAV code ──► output/
```

1. **Agent A** (`agent_a/`) parses `GPF_Bank_Statement_Q4_2022.pdf` (word coordinates), checks every running
   balance and the totals, asks the LLM to categorize each transaction (validated in code, cross-checked against
   keyword rules), and derives the purchases and sales.
2. **A2A conversation** (logged in `output/a2a_log.jsonl`): A asks B for marks -> B replies `input-required`
   asking for purchases and sales -> A answers on the same task/context -> B compares to the cent and returns a
   `completed` task whose artifact is the marks. A wrong answer keeps the task `input-required` with "matched k of n".
3. **Agent B** (`agent_b/`) reviews the marks (R1-R4) and computes the NAV and bridge from the Q3 files.

Layout: `common/` (money, JSON, config, A2A helpers) - `agent_a/` (pdf_parser, reconcile, reference, rules,
llm, categorizer, pipeline, server) - `agent_b/` (data, fx, handler, server, review, nav, close) - `tests/`.

## Outputs (`output/`)
`transactions.json`, `a2a_log.jsonl`, `marks.json`, `review.json`, `nav.json` as specified, plus
`reconciliation.json` (arithmetic proof and categorization checks). `main.py` prints a summary at the end.

## Tests
`pytest` runs offline. The LLM is replaced by in-process fakes and, for the full run, a local stand-in server
(`tests/fake_llm.py`) that speaks the OpenAI chat format - it exercises the real HTTP A2A servers and the real
LLM client code, but not a real model. Covered: PDF parsing and reconciliation (including tampering), LLM answer
validation / retry / fallback, the A2A handshake and its error cases, FX roll-back, review rules, NAV numbers,
and that a mis-categorized settlement breaks the bridge.

## Honest limitations
* **A real model is only exercised by `python main.py`.** Tests use a stand-in, so model-specific behaviour
  (rate limits, JSON-mode support, odd answers) is handled defensively - retries with back-off, JSON mode with
  automatic fallback, validation of every answer - but is not covered by an automated test.
* The review/NAV step is called by `main.py` as a library of Agent B, not over A2A. Only the marks exchange
  (the part the brief specifies) runs over the protocol.
* The PDF parser's column boundaries are tuned to this bank's layout; another bank needs new boundaries
  (the reconciliation would catch a mis-parse, not fix it). No OCR for scanned statements.
* The ECB rates are a small stored table (two dates) with the source URL, not a live feed.
* Only USD and EUR are supported; other currencies raise an error. Agent B keeps conversation state in memory.
* No authentication on the A2A endpoints, no streaming, no push notifications; `message/send` only.

## From prototype to product
Authenticated A2A (signed agent cards, OAuth), a persistent task store, and a third A2A skill so NAV also runs
behind the protocol; a live FX service and multi-currency support; per-bank statement adapters plus OCR; an
evaluation set of labelled transactions to track LLM accuracy and cost; human review UI for flagged marks and
LLM/rules disagreements; period roll-forward (Q4 outputs feed Q1), audit trail and Excel/PDF reporting.
