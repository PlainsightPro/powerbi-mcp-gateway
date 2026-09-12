# Monthly trend of a measure (template)

Use for "how did <measure> evolve over the last months" questions. Works on any model with a date
table that has an integer year-month column. The example below uses the Contoso Retail model.

## 1. Trend

```dax
EVALUATE
SUMMARIZECOLUMNS(
    'Date'[Year Month Id],
    FILTER(VALUES('Date'[Year Month Id]), 'Date'[Year Month Id] >= 202509 && 'Date'[Year Month Id] <= 202608),
    "Revenue", [Revenue],
    "Gross Margin %", [Gross Margin %]
)
ORDER BY 'Date'[Year Month Id]
```

Replace the measures with the ones the question is about; keep at most three so the trend stays
readable. Adapt the bounds to the window the user asked for (default: last 12 full months).

## 2. How to read it

- Say whether the level moved, the ratio moved, or both.
- Flag an incomplete last month.
- Offer the `top-movers` recipe when the user asks why.
