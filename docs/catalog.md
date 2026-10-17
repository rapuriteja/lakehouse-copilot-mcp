# Gold layer data catalog

_Generated from `catalog.yml`. Do not edit._

## Tables

### `gold.fact_sales`

Sales fact table. One row per order line (one product inside one order). Use it for revenue, units, discounts, margin, order counts and payment method analysis. Cancelled and returned lines are kept; filter with is_revenue = true for revenue.

- **Grain:** one row per order_item_id
- **Primary key:** `order_item_id`
- **Joins:** `gold.fact_sales.date_key = gold.dim_date.date_key`; `gold.fact_sales.customer_sk = gold.dim_customer.customer_sk`; `gold.fact_sales.product_sk = gold.dim_product.product_sk`

| Column | Type | Description |
|---|---|---|
| `order_item_id` | string | Unique id of the order line. Primary key. |
| `order_id` | string | Order id. One order can have several lines |
| `date_key` | int | Order date as yyyymmdd. Joins to gold.dim_date.date_key. |
| `order_ts` | timestamp | Exact time the order was placed (UTC). |
| `customer_sk` | bigint | Surrogate key of the customer version valid on the order date. Joins to gold.dim_customer.customer_sk. |
| `product_sk` | bigint | Surrogate key of the product. Joins to gold.dim_product.product_sk. |
| `payment_method` | string | How the customer paid. One of UPI, COD, Credit Card, Debit Card, Wallet, Net Banking. |
| `order_status` | string | Status of the order. One of Delivered, Shipped, Cancelled, Returned. |
| `channel` | string | Where the order was placed. App or Web. |
| `quantity` | int | Number of units of the product on this line. Always greater than zero. |
| `unit_price` | decimal(12,2) | List price of one unit in INR at the time of the order. |
| `discount_pct` | decimal(5,2) | Discount on this line in percent (0 to 100). Festive season discounts are higher. |
| `gross_amount` | decimal(14,2) | quantity * unit_price in INR |
| `discount_amount` | decimal(14,2) | Discount given on this line in INR. |
| `net_amount` | decimal(14,2) | Amount the customer paid for the line in INR (gross_amount - discount_amount). Basis of net revenue. |
| `is_revenue` | boolean | True when order_status is Delivered or Shipped. Only these lines count as revenue. |
| `cost_amount` | decimal(14,2) | quantity * cost_price in INR. What the product cost us. |
| `margin_amount` | decimal(14,2) | net_amount - cost_amount in INR. Gross margin (profit) of the line. |

### `gold.dim_customer`

Customer dimension with full history (SCD Type 2). A customer who moved city or was upgraded to Premium has one row per version. Use is_current = true for the latest details. Join from gold.fact_sales on customer_sk to get the city and segment the customer had on the order date.

- **Grain:** one row per customer version
- **Primary key:** `customer_sk`
- **Joins:** `gold.dim_customer.customer_sk = gold.fact_sales.customer_sk`

| Column | Type | Description |
|---|---|---|
| `customer_sk` | bigint | Surrogate key of this customer version. -1 is the unknown customer. |
| `customer_id` | string | Business id of the customer |
| `full_name` | string | First and last name. |
| `email` | string | Latest email address (Type 1 |
| `phone` | string | Latest Indian mobile number with +91 prefix. |
| `city` | string | City the customer lived in during this version, such as Mumbai, Bengaluru, Chennai. |
| `state` | string | Indian state of the city. |
| `region` | string | Region of India. One of North, South, East, West, Central. |
| `city_tier` | int | City tier. 1 = metro (Mumbai, Delhi, Bengaluru...), 2 = large city, 3 = smaller city. |
| `segment` | string | Customer segment. Premium or Regular. |
| `signup_date` | date | Date the customer created an account. |
| `effective_from` | date | First day this version is valid (inclusive). |
| `effective_to` | date | First day this version is no longer valid (exclusive). 9999-12-31 for the current version. |
| `is_current` | boolean | True for the latest version of the customer. |

### `gold.dim_product`

Product dimension. One row per product with category, brand and prices in INR.

- **Grain:** one row per product
- **Primary key:** `product_sk`
- **Joins:** `gold.dim_product.product_sk = gold.fact_sales.product_sk`

| Column | Type | Description |
|---|---|---|
| `product_sk` | bigint | Surrogate key of the product. -1 is the unknown product. |
| `product_id` | string | Business id of the product |
| `product_name` | string | Product name. |
| `category` | string | Top level category. One of Electronics, Fashion, Home & Kitchen, Beauty, Grocery, Books, Sports. |
| `sub_category` | string | Sub category, such as Smartphones, Sarees, Cookware, Sweets. |
| `brand` | string | Brand, such as Samsung, boAt, FabIndia, Prestige, Haldiram's. 'Unknown' when the source had no brand. |
| `unit_price` | decimal(12,2) | Current list price of one unit in INR. |
| `cost_price` | decimal(12,2) | Our cost of one unit in INR. |

### `gold.dim_date`

Calendar dimension. One row per day for 2024 and 2025, with year, quarter, month, weekday and the Diwali festive season flags.

- **Grain:** one row per calendar day
- **Primary key:** `date_key`
- **Joins:** `gold.dim_date.date_key = gold.fact_sales.date_key`

| Column | Type | Description |
|---|---|---|
| `date_key` | int | Date as yyyymmdd |
| `date` | date | Calendar date. |
| `year` | int | Calendar year |
| `quarter` | int | Calendar quarter 1 to 4. |
| `month` | int | Month number 1 to 12. |
| `month_name` | string | Month name |
| `year_month` | string | Year and month as yyyy-MM, like 2024-10. Good for monthly trends. |
| `day_of_month` | int | Day of the month 1 to 31. |
| `day_name` | string | Weekday name |
| `is_weekend` | boolean | True on Saturday and Sunday. |
| `is_festive_season` | boolean | True during the Diwali festive season (21 days before Diwali to 2 days after). |
| `is_diwali` | boolean | True on Diwali day itself (2024-11-01 and 2025-10-20). |

### `gold.agg_daily_sales`

Daily sales summary, pre-aggregated from gold.fact_sales. Fast for daily and festive-season trends. Revenue columns already exclude cancelled and returned orders.

- **Grain:** one row per day with at least one order
- **Primary key:** `date_key`

| Column | Type | Description |
|---|---|---|
| `date_key` | int | Day as yyyymmdd. |
| `date` | date | Calendar date. |
| `order_count` | bigint | Revenue orders (Delivered or Shipped) that day. |
| `units_sold` | bigint | Units sold on revenue orders. |
| `net_revenue` | decimal(16,2) | Net revenue in INR. |
| `gross_margin` | decimal(16,2) | Gross margin in INR. |
| `all_orders` | bigint | All orders that day |
| `cancelled_orders` | bigint | Orders with status Cancelled. |
| `returned_orders` | bigint | Orders with status Returned. |
| `is_festive_season` | boolean | True during the Diwali festive season. |

### `gold.agg_monthly_category_sales`

Monthly sales by product category. An order with products from two categories is counted once in each category's order_count.

- **Grain:** one row per year_month and category
- **Primary key:** `['year_month', 'category']`

| Column | Type | Description |
|---|---|---|
| `year` | int | Calendar year. |
| `month` | int | Month number 1 to 12. |
| `year_month` | string | Month as yyyy-MM. |
| `category` | string | Product category. |
| `order_count` | bigint | Revenue orders containing this category. |
| `units_sold` | bigint | Units sold. |
| `net_revenue` | decimal(16,2) | Net revenue in INR. |
| `gross_margin` | decimal(16,2) | Gross margin in INR. |

### `gold.agg_monthly_city_sales`

Monthly sales by customer city, using the city the customer lived in on the order date (point-in-time, from the SCD2 customer dimension).

- **Grain:** one row per year_month and city
- **Primary key:** `['year_month', 'city']`

| Column | Type | Description |
|---|---|---|
| `year` | int | Calendar year. |
| `month` | int | Month number 1 to 12. |
| `year_month` | string | Month as yyyy-MM. |
| `city` | string | Customer city on the order date. |
| `state` | string | Indian state. |
| `region` | string | Region of India. |
| `city_tier` | int | City tier 1 |
| `order_count` | bigint | Revenue orders. |
| `units_sold` | bigint | Units sold. |
| `net_revenue` | decimal(16,2) | Net revenue in INR. |
| `gross_margin` | decimal(16,2) | Gross margin in INR. |

## Metrics

| Metric | Definition | SQL |
|---|---|---|
| Net revenue | Money customers paid after discounts, for orders that were Delivered or Shipped. Cancelled and returned orders are excluded. In INR. | `SUM(CASE WHEN f.is_revenue THEN f.net_amount END)` |
| Gross merchandise value (GMV) | Value of revenue orders at list price, before discounts. In INR. | `SUM(CASE WHEN f.is_revenue THEN f.gross_amount END)` |
| Orders | Number of distinct revenue orders (Delivered or Shipped). | `COUNT(DISTINCT CASE WHEN f.is_revenue THEN f.order_id END)` |
| Units sold | Total quantity sold on revenue orders. | `SUM(CASE WHEN f.is_revenue THEN f.quantity END)` |
| Average order value (AOV) | Net revenue divided by the number of revenue orders. In INR. | `SUM(CASE WHEN f.is_revenue THEN f.net_amount END) / COUNT(DISTINCT CASE WHEN f.is_revenue THEN f.order_id END)` |
| Gross margin | Net revenue minus product cost, for revenue orders. In INR. | `SUM(CASE WHEN f.is_revenue THEN f.margin_amount END)` |
| Gross margin % | Gross margin as a percentage of net revenue. | `100.0 * SUM(CASE WHEN f.is_revenue THEN f.margin_amount END) / SUM(CASE WHEN f.is_revenue THEN f.net_amount END)` |
| Discount given | Total discount in INR on revenue orders. | `SUM(CASE WHEN f.is_revenue THEN f.discount_amount END)` |
| Average discount % | Discount amount as a percentage of gross amount, on revenue orders. | `100.0 * SUM(CASE WHEN f.is_revenue THEN f.discount_amount END) / SUM(CASE WHEN f.is_revenue THEN f.gross_amount END)` |
| Cancellation rate | Percentage of all orders (any status) that were cancelled. | `100.0 * COUNT(DISTINCT CASE WHEN f.order_status = 'Cancelled' THEN f.order_id END) / COUNT(DISTINCT f.order_id)` |
| Return rate | Percentage of all orders (any status) that were returned. | `100.0 * COUNT(DISTINCT CASE WHEN f.order_status = 'Returned' THEN f.order_id END) / COUNT(DISTINCT f.order_id)` |
| Active customers | Number of distinct customers with at least one revenue order. Count customer_id, not customer_sk, because one customer can have several SCD2 versions. | `COUNT(DISTINCT CASE WHEN f.is_revenue THEN c.customer_id END)` |

## Glossary

- **UPI:** Unified Payments Interface, India's real-time bank-to-bank mobile payment system (Google Pay, PhonePe, Paytm). The most common payment method in this data.
- **COD:** Cash on Delivery. The customer pays cash when the parcel arrives. Popular in tier 2 and tier 3 cities and has a higher cancellation rate.
- **Festive season:** The Diwali shopping season, from 21 days before Diwali until 2 days after. Flagged by gold.dim_date.is_festive_season. Orders and discounts rise sharply.
- **Diwali:** Festival of lights and the biggest shopping event of the year in India. Diwali was on 2024-11-01 and 2025-10-20 (gold.dim_date.is_diwali).
- **City tier:** Indian city classification. Tier 1 are metros (Mumbai, Delhi, Bengaluru, Chennai, Hyderabad, Kolkata, Pune, Ahmedabad), tier 2 are large cities, tier 3 are smaller cities. Column city_tier.
- **Customer segment:** Premium or Regular, stored in gold.dim_customer.segment. Customers can be upgraded to Premium; the upgrade creates a new SCD2 version.
- **Revenue order:** An order with status Delivered or Shipped (is_revenue = true). Cancelled and Returned orders are not revenue.
- **Region:** Part of India the customer lives in. One of North, South, East, West, Central.
- **Order line:** One product inside an order. gold.fact_sales has one row per order line, so an order with 3 products has 3 rows.

## Business rules

- **Which orders count as revenue:** Revenue counts only Delivered and Shipped orders (gold.fact_sales.is_revenue = true). Cancelled and Returned orders are excluded from net revenue, orders, units, AOV and margin, but they are included in cancellation rate and return rate.
- **Customer city at order time versus current city:** gold.dim_customer keeps history (SCD Type 2). Joining gold.fact_sales to gold.dim_customer on customer_sk gives the city and segment the customer had on the order date. To report on where customers live today, filter dim_customer with is_current = true. Count customers with COUNT(DISTINCT customer_id).
- **Diwali festive season dates:** Festive season 2024 runs from 2024-10-11 to 2024-11-03 (Diwali 2024-11-01). Festive season 2025 runs from 2025-09-29 to 2025-10-22 (Diwali 2025-10-20). Use gold.dim_date.is_festive_season to filter it.
- **Currency, time zone and data range:** All amounts are in Indian Rupees (INR). Timestamps are UTC. The data covers orders from 2024-01-01 to 2025-12-31.
- **Data quality and quarantined records:** Bad source records never reach Gold. Silver removes duplicates and moves rows that fail data quality rules (missing or unknown customer, invalid payment method, zero or negative quantity, unknown product, line of a rejected order) to quarantine tables. Warnings such as malformed emails or a missing brand are counted but the row is kept.
