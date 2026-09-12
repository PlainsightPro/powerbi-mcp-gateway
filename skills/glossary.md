# Contoso finance glossary (example)

This folder is an example. Point `PBIMCP_SKILLS_DIR` at your own folder (or pass `-SkillsDir` to the
deploy script) to replace it with your organisation's vocabulary, models and recipes.

## Core P&L measures (table 'GL Account')

| Term | Measure | Meaning and sign |
|---|---|---|
| Revenue | [Revenue] | Billable revenue. Positive. |
| Direct cost | [Direct Cost] | Cost tied to delivery. Negative. |
| Gross margin | [Gross Margin] | [Revenue] + [Direct Cost]. |
| Overhead (net) | [Overhead] | Indirect income and cost. Negative when it is a cost. |
| Indirect cost (positive) | [Indirect Cost] | The same overhead as a positive amount. Use it when the user talks about indirect costs growing. |
| EBITDA | [EBITDA] | [Gross Margin] + [Overhead]. Positive is profit. |
| Ratios | [Gross Margin % Revenue], [EBITDA % Revenue], [Indirect Cost % Revenue] | Fractions (0.129 = 12.9 %). |

Year-over-year block: [Indirect Cost This Year], [Indirect Cost Last Year], [Indirect Cost Growth]
(positive = cost grew). "This year" is the year in filter context; put one year in context first.

## Which table answers what

- 'Transactions' (fact): [Transaction Amount] (natural sign), [Transaction Count]. Drill-down columns:
  [Description], [Counterparty], [Transaction Date], [Legal Entity].
- 'GL Account' (dimension): [GL Account Code], [GL Account Name], [GL Account Type], [P&L Subcategory].
- 'Cost Category': management classification (direct vs indirect) with [Financial Breakdown].
- 'Customer': [Customer Name]; revenue per customer through the allocation table.
- 'Date': default date table. [Year] (integer), [Year Month] (text), [Year Month Id] (integer yyyymm).

## Vocabulary the business uses

- "Indirect costs are rising" -> [Indirect Cost] per month, then drivers by GL account and by cost
  category, then the transaction detail behind the biggest movers.
- "Margin" without qualifier -> [Gross Margin]; "margin on revenue" -> [Gross Margin % Revenue].
- "Result" -> [EBITDA] unless the user says "profit before tax".
- "Entity" -> filter 'Transactions'[Legal Entity].
