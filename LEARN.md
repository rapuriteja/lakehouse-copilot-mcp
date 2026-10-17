# Learn this project

A guide to understand every part of Lakehouse Copilot well enough to explain it in an
interview. Read it with the code open. Each section says **what** the part does, **why** it is
built that way, **where** the code is, and ends with interview questions.

Suggested order: run the project once (README, "Run it locally"), then read sections 1 to 8.

---

## 1. The big picture

Data flows in one direction:

```
raw files -> Bronze -> Silver -> Gold -> (SQL guard) -> tools -> MCP server / copilot agent
                         |
                     quarantine
catalog.yml -> docs + search index -> tools
```

- The **data engineering part** (sections 2 to 4) makes the data correct and trustworthy.
- The **AI part** (sections 5 to 8) lets people ask questions about that data safely.

The AI part is only as good as the data part. If Gold has duplicates or the revenue rule is
unclear, the copilot will give wrong answers confidently. That is why most of the project is
data engineering.

**Local-first design.** Every cloud service has a free local stand-in behind the same interface:

| Interface | Local | Azure |
|---|---|---|
| `Lakehouse.write()` | Parquet | Delta table in Unity Catalog |
| `Retriever.search()` | TF-IDF, or TF-IDF + embeddings (`hybrid`) | Azure AI Search |
| `engine.execute()` | DuckDB | Databricks SQL |
| `ChatModel.chat()` | OpenRouter, or offline planner without a key | OpenRouter (key from Key Vault) |

The choice is made in `config.py` from `.env`. Tests never need cloud keys.

---

## 2. Synthetic data

**Where:** `src/lakehouse_copilot/data_gen/`

- `reference.py` holds the "world": 30 Indian cities with state, region and tier, 7 product
  categories with brands, price ranges and margins, payment methods and Diwali dates.
- `generator.py` builds customers, products, orders and order lines with a fixed random seed.
  Order volume rises around Diwali (`festive_factor`).
- It injects **dirty records** on purpose (duplicates, missing customers, unknown products,
  invalid payment values, negative quantities, two date formats, bad emails).
- It writes a second customer batch on 2025-07-01 with real changes (moves, upgrades, email
  changes) and also rows that did not change at all.
- `_manifest.json` records exactly how many of each problem were injected. The pipeline tests
  compare against it, so we know the pipeline catches every bad record and nothing more.

**Why a seed?** Same seed, same data, same numbers. Tests and evaluation results are
reproducible, which is required before you can claim any number.

---

## 3. The medallion pipeline

**Where:** `src/lakehouse_copilot/pipeline/`

### Bronze (`bronze.py`)
Reads every column as a **string** and adds `_source_file`, `_batch_id` and `_ingest_ts`.
Nothing can fail at this stage, so no data is ever lost on the way in. If a parse rule is wrong,
you fix Silver and re-run from Bronze; you never need to re-fetch the source.

### Silver (`silver.py`, `dq.py`, `scd2.py`)
- **Safe casting.** Spark 4 runs in ANSI mode, where a bad cast throws an error. `try_cast` and
  `try_to_timestamp` return null instead, and a DQ rule then catches the null. Two date formats
  are handled with `coalesce` of two parsers.
- **Standardising values.** `upi`, `UPI ` and `Cash on Delivery` become `UPI` and `COD`
  (`PAYMENT_METHOD_MAP`).
- **Deduplication** with `row_number()` over the key, keeping the latest row. Rows with a null key
  are not deduplicated together, because two unknown keys are not the same record.
- **Data quality rules** (`dq.py`): each `Rule` is a name, a SQL condition and an action.
  `quarantine` moves failing rows to a `quarantine_<table>` table with the rule name;
  `warn` only counts them. Results go to `silver.dq_results`. Rules are data, not code, so adding
  one is one line.
- **Referential checks**: an order must have a known customer; an order line must have a known
  order and product. A line whose order was rejected is rejected too.

### SCD Type 2 (`scd2.py`)
SCD2 keeps **history**: when a tracked column (city, segment) changes, the old row is closed and
a new version is opened.

| customer_id | city | effective_from | effective_to | is_current |
|---|---|---|---|---|
| C00042 | Pune | 2024-03-10 | 2025-07-01 | false |
| C00042 | Chennai | 2025-07-01 | 9999-12-31 | true |

Details worth knowing:
- `apply_scd2(current, incoming)` is a **pure function**: it takes the current history and a new
  batch and returns the new history. That makes it easy to test and to replay batches in order.
- **Half-open interval** `[effective_from, effective_to)`: a date belongs to exactly one version,
  with no gaps and no overlaps.
- **Type 1 columns** (email, phone) are overwritten on every version, because we only care about
  the latest value.
- A batch row that changes nothing creates nothing (36 such rows in the data).

### Gold (`gold.py`)
A **star schema**: one fact table in the middle, dimensions around it.

- `fact_sales`: grain is one order line. Amounts: `gross = quantity * unit_price`,
  `discount`, `net = gross - discount`, `cost`, `margin = net - cost`. `is_revenue` is true for
  Delivered and Shipped orders only.
- `dim_customer` (SCD2), `dim_product`, `dim_date` (with `is_festive_season` and `is_diwali`).
- **Surrogate keys** are `xxhash64` of the natural key (plus `effective_from` for customers), so
  they are the same on every run.
- **Point-in-time join**: a fact row gets the customer version valid on the order date
  (`order_date >= effective_from AND order_date < effective_to`).
- **Unknown member**: a fact row whose customer or product cannot be found gets key `-1` instead of
  being dropped by an inner join.
- **Aggregates** (`agg_daily_sales`, `agg_monthly_category_sales`, `agg_monthly_city_sales`) for
  fast dashboards.

### Storage (`storage.py`) and running (`run.py`)
`Lakehouse` writes Parquet locally or `saveAsTable` to `catalog.layer.table` on Databricks. The
pipeline code does not know which. `run.py` runs the layers in order and saves a report with row
counts and timing.

### Interview questions
1. **Why keep Bronze as strings?** So ingestion never fails and the raw data is always
   replayable. Parsing errors become data quality results in Silver instead of lost rows.
2. **Quarantine versus dropping bad rows?** Quarantine keeps the bad rows with the reason, so
   someone can fix the source and you can prove how many rows were removed and why.
3. **How do you test SCD2?** With tiny hand-made batches, one case per test: a first load opens
   versions; a city move closes the old version and opens a new one; an email change updates every
   version; re-applying the same batch changes nothing (`tests/test_scd2.py`).
4. **Why surrogate keys instead of customer_id in the fact?** One customer has several versions;
   the surrogate key points to the exact version, which gives the city at order time.

---

## 4. Catalog as code

**Where:** `src/lakehouse_copilot/catalog/`

`catalog.yml` is the single source of truth for meaning: table and column descriptions, a `pii`
flag per column, 15 metrics with their SQL, 9 glossary terms and 5 business rules. From it:

- `docs.py` builds `docs/catalog.md` and **chunks** for search (one chunk per table, column group,
  metric, glossary term and rule). Small, focused chunks retrieve better than whole documents.
- the SQL guard learns which tables exist and which columns are PII;
- on Databricks, `databricks_job.py publish-docs` writes Unity Catalog comments and `pii` tags.

**Why?** Metric definitions in one reviewed file mean the dashboard, the copilot and the
engineers all use the same definition of "net revenue".

A lesson from this project: in YAML, `{description: a, b}` is cut at the comma. Thirteen
descriptions were silently shortened until a test that checks for empty values found it
(`tests/test_catalog.py`).

---

## 5. Retrieval (RAG)

**Where:** `src/lakehouse_copilot/rag/`

RAG means: before answering, **retrieve** relevant documents and give them to the model, so it
answers from facts instead of memory.

- **Local:** TF-IDF with word pairs (bigrams) and cosine similarity. Fast, free and good enough for
  52 short chunks.
- **Azure:** AI Search **hybrid search**. Keyword search (BM25) is good at exact words like
  `is_revenue`; vector search (embeddings) is good at meaning ("money we made" ≈ "net revenue").
  The two ranked lists are merged with **Reciprocal Rank Fusion**, then the **semantic ranker**
  re-orders the top results with a language model.
- **Query rewriting** adds the official names of synonyms ("AOV" → "average order value") and
  merges follow-ups ("and in 2025?" uses the previous question). On the held-out test set it raised
  hit@5 from 0.95 to 1.00.
- **Local hybrid** (`RETRIEVER=hybrid`): TF-IDF plus vector search with embeddings from OpenRouter
  (`openai/text-embedding-3-small`), merged with Reciprocal Rank Fusion. Measured on the held-out
  set: MRR@5 went from 0.91 (TF-IDF only) to 0.96, hit@1 from 0.85 to 0.95.

### Interview questions
1. **Why hybrid instead of only vectors?** Column names and codes (COD, UPI, `date_key`) are exact
   tokens that embeddings blur; BM25 finds them. Meaning-based questions need vectors. Hybrid gets
   both.
2. **What is MRR?** Mean Reciprocal Rank: for each question, 1 divided by the position of the
   first correct document (0 if not found), averaged. 0.91 means the right doc is usually first.

---

## 6. SQL safety and the MCP server

**Where:** `src/lakehouse_copilot/sql/`, `tools.py`, `mcp_server/`

**MCP** (Model Context Protocol) is a standard way to give AI clients tools. Write the server once
and Claude Desktop, VS Code or Cursor can all use it.

The four tools live in `tools.py`, shared by the MCP server and the agent, so both behave the same.

The SQL guard parses SQL into a tree with `sqlglot` and checks the tree, not the text:
1. exactly one statement, and it must be a SELECT (or UNION of SELECTs);
2. no write or admin nodes anywhere (INSERT, DROP, PRAGMA, COPY...);
3. no file-reading functions (`read_csv`, `glob`...);
4. only allowed `gold.*` tables (CTE names are allowed too);
5. no PII columns, and no `SELECT *` on a table that has PII (`COUNT(*)` is fine);
6. a `LIMIT` is added or lowered to the maximum.

Then the **engine** adds a second wall: DuckDB opens read-only, external access is off, the
configuration is locked, there is a timeout, and PII columns are not in the serving file at all.
This is **defence in depth**: if one layer has a bug, the next still protects the data.

Errors are returned to the model as tool errors with the reason, so it can correct the query.

### Interview questions
1. **Why parse SQL instead of using a regex for "DROP"?** A regex is easy to trick (comments,
   different case, a CTE). A parser sees the real structure.
2. **Is the guard enough on its own?** No. Use a read-only connection and least-privilege
   credentials too; on Databricks, a service principal with only `SELECT` on the gold schema.

---

## 7. The copilot agent

**Where:** `src/lakehouse_copilot/agent/`

Steps for each question: rewrite → retrieve → answer → cite.

- **LLM mode** (`copilot.py`, `llm.py`): the model is reached through **OpenRouter**, one API in
  front of many models. It speaks the OpenAI chat API, so the official OpenAI SDK works with
  `base_url=https://openrouter.ai/api/v1`, and changing model is just a different `LLM_MODEL`
  string. Each answer records its LLM calls, tokens and the cost OpenRouter reports.
  The loop is a **tool-calling loop**. The model gets the question,
  the docs and the tool list; it calls tools; results (or errors) go back as `tool` messages;
  this repeats until it answers or hits `max_steps` (so it can never loop forever).
- **Offline mode** (`semantic.py`): a small **semantic layer**. It finds metrics by name and
  synonym, dimensions ("by city"), filters (years, values read from the real data like city names,
  "festive season", "tier 2 cities") and ranking ("top 5"), then writes SQL from the metric
  definitions. It needs no LLM and is very fast.
- **Citations** (`CopilotAnswer.sources`) come from the tables in the SQL that actually ran and the
  docs actually retrieved. The model never writes its own citations, so they cannot be invented.
- **Formatting** (`format.py`): Indian number grouping (`12,73,33,616.50`) and lakh/crore.

### Interview questions
1. **How do you stop an agent from hallucinating numbers?** Numbers only come from `run_sql`
   results; the prompt forbids invented numbers; citations are built by code; and the eval checks
   the SQL result against a gold query.
2. **Why keep an offline planner when LLMs exist?** It is free, instant and deterministic, it is a
   baseline to measure the LLM against, and it handles the most common questions without any cost.
3. **Why OpenRouter instead of one provider's API?** One key and one API for many models, so I
   can benchmark cheap models on my golden set and switch by changing a string. The trade-off is
   one more company in the data path, so for sensitive data a direct provider or a private Azure
   OpenAI deployment can still be switched on (`LLM_PROVIDER=azure`).

---

## 8. Evaluation

**Where:** `src/lakehouse_copilot/evals/`, results in `evals/results/`

- 40 golden questions, each with the expected doc and a gold SQL query.
- **Dev split** (20) used while building; **test split** (20) held out. Reporting only dev would
  hide overfitting: dev is 100% but the honest number is the test 80%.
- **Execution accuracy** runs both the copilot's SQL and the gold SQL and compares results
  (numbers within 0.01; order matters only for rankings). Comparing results instead of SQL text
  is fair because many different SQL queries are correct.
- Retrieval: hit@1/3/5 and MRR@5, for the raw and the rewritten query.

### Interview question
**Why 80% and not 100%?** The four failures are new wording the rules had not seen. I left them
unfixed so the test number stays an honest estimate of how it does on new questions, and wrote
the failure analysis in `evals/README.md`.

### LLM mode results and model choice (OpenRouter)
- `lhc bench` runs several models through the same agent on the same 40 questions and records
  accuracy, the cost OpenRouter reports, and latency. I compared GPT-4o-mini, Gemini 2.5 Flash
  and DeepSeek Chat. **Gemini 2.5 Flash won: 85% on held-out questions** (offline planner 80%),
  $0.044 for all 40 questions, 3.1 s median per question.
- The **first** benchmark was worse than the offline planner for every model. The dev failures
  showed why: the model guessed table names, and it joined two pre-aggregated tables, which double
  counts. Putting the table list with columns into the system prompt, and a rule to start from
  `fact_sales`, fixed most of it. I only looked at dev failures to make the fix.
- LLM scores move a little between runs even at temperature 0, so I report the run I measured and
  say so.

### Interview questions
1. **How did you choose the LLM?** On my own golden set, not on public leaderboards: held-out
   execution accuracy first, then cost, then latency. Gemini 2.5 Flash had the best test accuracy
   (85%) at about $0.001 per question. GPT-4o-mini was cheaper per call but only 45% here.
2. **Your LLM was worse than rules at first. What did you do?** I read the failures on the dev
   split. They were agent bugs, not model limits: missing schema in the prompt, and joins across
   aggregate tables. I fixed the prompt, re-ran dev, then ran the held-out test once.

---

## 9. Infrastructure and CI/CD

**Where:** `infra/terraform/`, `databricks.yml`, `resources/`, `.github/workflows/`

- **Terraform** creates all Azure resources from code, so environments are repeatable and
  reviewed in pull requests. Highlights: storage with shared keys disabled (Azure AD only), Key
  Vault with RBAC, a Databricks **access connector** (a managed identity) that Unity Catalog uses
  to reach storage without secrets, and diagnostic settings to Log Analytics.
- **Databricks Asset Bundle** describes the job, schemas and volume as YAML next to the code.
  `databricks bundle deploy -t dev|prod` builds the wheel and deploys. Each layer is its own task,
  so a failure in Gold can be retried without re-running Bronze.
- **CI** on every push: lint, tests on two Python versions, the full pipeline with evals, and
  `terraform validate`. **Deploy** is manual and uses **OIDC**: GitHub proves its identity to
  Azure with a short-lived token, so no passwords are stored in GitHub.

### Interview questions
1. **How do you promote from dev to prod?** Same bundle and Terraform code, different target and
   variables; deploy is a manual workflow, and the prod GitHub environment can require a reviewer
   before it runs.
2. **Where do secrets live?** Key Vault in Azure and `.env` (git-ignored) locally. Databricks
   jobs read Key Vault through a **Key Vault-backed secret scope** (`{{secrets/scope/key}}` in the
   cluster's environment variables, redacted in logs), and the app reads it with `az login`. CI
   uses OIDC, so there is no long-lived Azure secret to leak, and `scripts/scan_secrets.py`
   checks the code and the full git history for keys before every push.

---

## 10. Commands to remember

```bash
lhc generate | pipeline | index | eval     # build everything
lhc ask "question" -v                      # answer with SQL and trace
lhc sql "SELECT ..."                       # run SQL through the guard
lhc search "festive season"                # test retrieval
lhc mcp [--http]                           # MCP server
lhc ui                                     # chat UI
pytest -q && ruff check .                  # quality checks
```
