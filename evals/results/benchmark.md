# Model benchmark

- 40 golden questions, retriever `local-hybrid(openai/text-embedding-3-small)`
- Accuracy = execution accuracy (same result rows as the gold SQL)
- Cost = OpenRouter's reported cost for the whole run, in US dollars

| Model | Dev | Test (held out) | All | Cost per run | Cost per question | Median latency | Errors |
|---|---|---|---|---|---|---|---|
| `offline planner` | 20/20 (100%) | 16/20 (80%) | 36/40 (90%) | free | free | 0.39 s | 0 |
| `openai/gpt-4o-mini` | 13/20 (65%) | 9/20 (45%) | 22/40 (55%) | $0.0250 | $0.00063 | 5.55 s | 0 |
| `google/gemini-2.5-flash` **(chosen)** | 17/20 (85%) | 17/20 (85%) | 34/40 (85%) | $0.0441 | $0.00110 | 3.08 s | 0 |
| `deepseek/deepseek-chat` | 17/20 (85%) | 15/20 (75%) | 32/40 (80%) | $0.0493 | $0.00123 | 7.55 s | 0 |
