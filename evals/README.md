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
LLM agent (OpenRouter) is built to close.

## LLM mode and model choice (OpenRouter)

```bash
lhc bench --models openai/gpt-4o-mini,google/gemini-2.5-flash,deepseek/deepseek-chat
lhc eval --model <chosen model>      # full report: results/eval_llm_<model>.md
```

`lhc bench` runs every model through the same agent, tools, retriever and 40 questions, and
records for each one: execution accuracy per split, the cost OpenRouter reported for the whole
run, the median latency per question and the number of questions that failed with an API error.
It picks the best held-out (test) accuracy, then the lowest cost. The rewriting step and every
tool-calling turn count toward cost and latency.

**Status: not measured yet.** The comparison table goes here after the first real run.
