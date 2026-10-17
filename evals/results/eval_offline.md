# Evaluation results

- Copilot mode: `offline`
- Retriever: `local-tfidf`, SQL engine: `duckdb`
- Golden questions: 40 (dev = used while building, test = held out)

## Retrieval (catalog docs)

| Split | Query | Hit@1 | Hit@3 | Hit@5 | MRR@5 |
|---|---|---|---|---|---|
| dev | raw | 0.85 | 0.90 | 1.00 | 0.90 |
| dev | rewritten | 0.85 | 0.90 | 1.00 | 0.90 |
| test | raw | 0.85 | 0.90 | 0.95 | 0.89 |
| test | rewritten | 0.85 | 0.95 | 1.00 | 0.91 |
| all | raw | 0.85 | 0.90 | 0.97 | 0.89 |
| all | rewritten | 0.85 | 0.93 | 1.00 | 0.91 |

## Text-to-SQL execution accuracy

| Split | Correct | Accuracy |
|---|---|---|
| dev | 20/20 | 100% |
| test | 16/20 | 80% |
| all | 36/40 | 90% |

Median latency per question: 0.012 s

## Per question

| Id | Question | Correct | Note |
|---|---|---|---|
| d01 | What was the total net revenue in 2024? | yes |  |
| d02 | How many orders did we get in 2025? | yes |  |
| d03 | Top 5 cities by net revenue in 2025 | yes |  |
| d04 | Which payment method has the most orders? | yes |  |
| d05 | What is the average order value for UPI vs COD? | yes |  |
| d06 | Monthly net revenue trend in 2025 | yes |  |
| d07 | Net revenue by category during the Diwali festive season 2024 | yes |  |
| d08 | Which category has the highest gross margin percentage? | yes |  |
| d09 | What is the cancellation rate by payment method? | yes |  |
| d10 | How many customers placed an order in Chennai? | yes |  |
| d11 | Net revenue 2024 vs 2025 | yes |  |
| d12 | How many orders were cancelled in October 2025? | yes |  |
| d13 | Units sold by category for tier 3 cities | yes |  |
| d14 | Which day of the week has the most orders? | yes |  |
| d15 | Top 3 brands in Fashion by net revenue | yes |  |
| d16 | What is the return rate in 2025? | yes |  |
| d17 | Net revenue by channel | yes |  |
| d18 | Gross margin from Premium customers in 2025 | yes |  |
| d19 | Average discount percentage during the festive season | yes |  |
| d20 | Net revenue in the South region in 2024 | yes |  |
| t01 | How much money did we make in 2025 after discounts? | no |  |
| t02 | Give me the number of orders placed through the website in 2024. | yes |  |
| t03 | Which state brought in the most sales? | yes |  |
| t04 | What was the GMV during Diwali season 2025? | yes |  |
| t05 | Show the average basket size for each customer segment. | yes |  |
| t06 | What share of COD orders got cancelled? | no |  |
| t07 | List the five best-selling products by units in 2025. | no |  |
| t08 | How many unique buyers did we have in 2024? | yes |  |
| t09 | Quarterly net revenue for 2025. | yes |  |
| t10 | Which region has the lowest average order value? | yes |  |
| t11 | Total discount given on Electronics in 2024. | yes |  |
| t12 | Compare revenue from tier 1 and tier 2 cities. | yes |  |
| t13 | What was our gross profit from Bengaluru customers in 2025? | yes |  |
| t14 | How many orders were returned in 2024? | yes |  |
| t15 | What was the revenue on Diwali day in 2024? | no |  |
| t16 | Revenue split by payment mode for Grocery. | yes |  |
| t17 | Which month of 2024 had the highest number of orders? | yes |  |
| t18 | What is the margin rate for the Books category? | yes |  |
| t19 | How many orders did Premium customers place on weekends in 2025? | yes |  |
| t20 | Net revenue from Hyderabad by sub category in 2025. | yes |  |
