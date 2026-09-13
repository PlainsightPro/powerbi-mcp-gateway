# Contoso Retail glossary (EXAMPLE)

This file is the deployment's business vocabulary. It is returned by `get_business_context` and fed
to the DAX generator with every question, so write it for a reader who has never seen the models:
which measure answers which question, sign conventions, which date table to use, known traps.
The content below describes a fictional company. See docs/skills-authoring.md for the pattern.

## Key measures (table 'Sales')

| Business term | Measure | Meaning and sign |
|---|---|---|
| Revenue, sales, turnover | [Revenue] | Net invoiced amount excluding VAT. Positive. |
| Units sold | [Units] | Quantity invoiced. |
| Cost of goods | [COGS] | Cost of the units sold. Positive amount; subtract it from revenue. |
| Gross margin | [Gross Margin] | [Revenue] - [COGS]. |
| Margin % | [Gross Margin %] | [Gross Margin] / [Revenue], a fraction (0.32 = 32 %). |
| Orders | [Orders] | Distinct invoices. |
| Average basket | [Average Order Value] | [Revenue] / [Orders]. |

Year-over-year block: [Revenue LY], [Revenue YoY %]. "This year" is the year in filter context.

## Which table answers what

- 'Sales' (fact): one row per invoice line. Drill-down columns: [Invoice Number], [Invoice Date].
- 'Product' (dimension): [Category], [Subcategory], [Product Name], [Brand].
- 'Store' (dimension): [Store Name], [Region], [Country], [Store Format].
- 'Customer' (dimension): [Customer Segment], [Loyalty Tier].
- 'Date': the only date table. [Year] (integer), [Year Month] (text), [Year Month Id] (integer yyyymm).

## Vocabulary the business uses

- "Like-for-like" -> compare stores open in both periods: filter 'Store'[Comparable] = TRUE.
- "Season" -> 'Date'[Season] (Spring, Summer, Autumn, Winter), not calendar quarters.
- "Region" without qualifier -> 'Store'[Region]; "market" -> 'Store'[Country].
- Returns are negative invoice lines; [Revenue] already nets them.

## Contoso Inventory (table 'Stock')

The second curated model, same 'Product' and 'Date' dimensions, plus 'Warehouse' ([Warehouse Name], [Region]).

| Business term | Measure | Meaning and sign |
|---|---|---|
| Stock, units on hand | [Stock Units] | Units in the warehouse at the end of the period. |
| Stock value | [Stock Value] | [Stock Units] at cost. |
| Days of cover | [Days of Cover] | [Stock Units] / average daily units sold; a number of days. |
| Stock-outs | [Stock-out Days] | Days in the period with zero units on hand. |

'Stock' (fact): one row per product, warehouse and day. Stock measures are end-of-period balances,
not sums: for a month use the last day, never SUM over days.

## Traps

- 'Product'[Category] and 'Product'[Subcategory] share names for some rows; always show both.
- The current month is incomplete until the nightly load has run.
