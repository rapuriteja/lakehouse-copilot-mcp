# Evaluation

`lhc eval` scores the copilot on 40 golden questions in
[`golden.yaml`](../src/lakehouse_copilot/evals/golden.yaml). Each question has the catalog
docs that answer it and a hand-written gold SQL query.

- **dev (20):** used while building the offline planner and the retriever.
- **test (20):** held out. They were written before the first run and never used for tuning.
  The test numbers below come from that first run.

| What | How it is measured |
|---|---|
| Retrieval hit@k | At least one relevant doc is in the top k results |
| Retrieval MRR@5 | Mean of 1 / rank of the first relevant doc |
| Text-to-SQL execution accuracy | The copilot's SQL returns the same rows as the gold SQL (order-insensitive unless the question asks for a ranking or trend, numbers within 0.01) |

Latest results (offline planner + local TF-IDF): [eval_offline.md](results/eval_offline.md).

## Why the offline planner failed 4 held-out questions

| Id | Question | What went wrong |
|---|---|---|
| t01 | How much money did we make in 2025 after discounts? | The word "discounts" matched the discount metric. |
| t06 | What share of COD orders got cancelled? | "share ... got cancelled" is not a known phrase for the cancellation rate. |
| t07 | List the five best-selling products by units in 2025. | "five" written as a word is not read as a number, so it returned the top 1. |
| t15 | What was the revenue on Diwali day in 2024? | The word "day" was read as "group by day". |

These are the limits of keyword rules: they match words, not meaning. This is the gap the
LLM agent (`LLM_PROVIDER=azure`) is built to close. Run `lhc eval` with Azure OpenAI
configured to measure it; the report is saved as `results/eval_llm_<deployment>.md`.
