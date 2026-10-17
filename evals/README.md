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

### Results (measured on 2026-10-17, retriever `local-hybrid` with OpenRouter embeddings)

Final run, after the prompt fix described below ([benchmark.md](results/benchmark.md)):

| Model | Dev | Test (held out) | All | Cost per run (40 questions) | Cost per question | Median latency |
|---|---|---|---|---|---|---|
| offline planner (no LLM) | 20/20 (100%) | 16/20 (80%) | 36/40 (90%) | free | free | 0.39 s |
| `openai/gpt-4o-mini` | 13/20 (65%) | 9/20 (45%) | 22/40 (55%) | $0.0250 | $0.00063 | 5.55 s |
| **`google/gemini-2.5-flash` (chosen)** | 17/20 (85%) | **17/20 (85%)** | 34/40 (85%) | $0.0441 | $0.00110 | 3.08 s |
| `deepseek/deepseek-chat` | 17/20 (85%) | 15/20 (75%) | 32/40 (80%) | $0.0493 | $0.00123 | 7.55 s |

No question failed with an API error. **Gemini 2.5 Flash is the default model** (`LLM_MODEL`):
it had the best held-out accuracy and the lowest latency, for about $0.001 per question.
GPT-4o-mini is cheaper per question but much less accurate here.

Then `lhc eval` with the chosen model ([full report](results/eval_llm_google_gemini-2.5-flash.md)):

| Split | Offline planner | LLM mode (Gemini 2.5 Flash) |
|---|---|---|
| dev | 20/20 (100%) | 18/20 (90%) |
| test (held out) | 16/20 (80%) | **17/20 (85%)** |
| all | 36/40 (90%) | 35/40 (88%) |

That run made 120 LLM calls (107,872 tokens in, 5,400 out) and cost $0.0447.

### What changed between the first and the final run

The first benchmark ([benchmark_first_run.md](results/benchmark_first_run.md)) used the
original prompt. Every model scored below the offline planner on held-out questions
(GPT-4o-mini 70%, Gemini 2.5 Flash 65%, DeepSeek 75%). Reading the **dev** failures showed two
real bugs in the agent, not in the models:

1. The model did not know the table and column names, so it guessed (`gold.fact_revenue`,
   `gold.dim_payment_method`) and spent tool calls on errors. Fix: the system prompt now lists
   every Gold table with its grain and columns, built from the catalog.
2. The model joined two aggregate tables on `year_month` (for example category sales with city
   sales), which double counts. Fix: the prompt says to start from `gold.fact_sales` with the
   dimensions, use an `agg_*` table only when it already has every column needed, and never join
   `agg_*` tables.

Only dev failures were used to make these fixes; the test questions were not changed.

### Why LLM mode still misses some questions

The 5 misses in the final `lhc eval` run:

| Id | What went wrong |
|---|---|
| d13 | Gave a text answer without running SQL. |
| d14, t19 | Counted all orders, not only revenue orders (`is_revenue = true`). |
| t02 | Guessed the channel value `'Website'`; the data uses `'Web'`. |
| t17 | Summed `order_count` from the monthly category table, so an order with two categories counted twice. |

### Things to know about these numbers

- **They move between runs**, even at temperature 0, because providers do not promise
  identical output. GPT-4o-mini scored 80% on dev in one run and 65% in another with the same
  prompt. With 20 test questions, one question is 5 points.
- The offline planner is still a strong baseline on this golden set because its rules were
  written for this catalog. LLM mode wins on wording it has never seen (it fixed all four
  offline failures: t01, t06, t07 and t15).
- Hybrid retrieval with OpenRouter embeddings (`openai/text-embedding-3-small`) raised held-out
  MRR@5 from 0.91 (TF-IDF) to 0.96, hit@5 stays 1.00
  ([report](results/hybrid/eval_offline.md)).
- The whole OpenRouter phase (connection checks, two benchmarks, one dev rerun and this eval)
  cost about $0.40.
