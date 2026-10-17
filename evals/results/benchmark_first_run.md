# Model benchmark (first run, before the prompt fix)

- 40 golden questions, retriever `local-hybrid(openai/text-embedding-3-small)`
- Accuracy = execution accuracy (same result rows as the gold SQL)
- Cost = OpenRouter's reported cost for the whole run, in US dollars

| Model | Dev | Test (held out) | All | Cost per run | Cost per question | Median latency | Errors |
|---|---|---|---|---|---|---|---|
| `offline planner` | 20/20 (100%) | 16/20 (80%) | 36/40 (90%) | free | free | 0.38 s | 0 |
| `openai/gpt-4o-mini` | 14/20 (70%) | 14/20 (70%) | 28/40 (70%) | $0.0302 | $0.00075 | 6.56 s | 0 |
| `google/gemini-2.5-flash` | 13/20 (65%) | 13/20 (65%) | 26/40 (65%) | $0.0644 | $0.00161 | 4.31 s | 0 |
| `deepseek/deepseek-chat` **(chosen)** | 16/20 (80%) | 15/20 (75%) | 31/40 (78%) | $0.0828 | $0.00207 | 15.63 s | 0 |
