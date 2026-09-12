# Recipe: EBITDA bridge (what moved the result)

Typical question: "How did EBITDA evolve and where does the difference come from?" Model: Finance.

## 1. Monthly P&L waterfall components

```dax
EVALUATE
SUMMARIZECOLUMNS(
    'Date'[Year Month Id],
    FILTER(VALUES('Date'[Year Month Id]), 'Date'[Year Month Id] >= 202501),
    "Revenue", [Revenue],
    "Direct Cost", [Direct Cost],
    "Gross Margin", [Gross Margin],
    "Overhead", [Overhead],
    "EBITDA", [EBITDA],
    "EBITDA % Revenue", [EBITDA % Revenue]
)
ORDER BY 'Date'[Year Month Id]
```

[Gross Margin] = [Revenue] + [Direct Cost]; [EBITDA] = [Gross Margin] + [Overhead]. Costs are
negative, so the bridge from revenue to EBITDA is a plain sum.

## 2. Year totals per legal entity

```dax
EVALUATE
SUMMARIZECOLUMNS(
    'Transactions'[Legal Entity],
    'Date'[Year],
    FILTER(VALUES('Date'[Year]), 'Date'[Year] >= 2025),
    "Revenue", [Revenue],
    "Gross Margin", [Gross Margin],
    "Overhead", [Overhead],
    "EBITDA", [EBITDA]
)
ORDER BY 'Transactions'[Legal Entity], 'Date'[Year]
```

## 3. Explain the delta between two periods

Compute the components for both periods and present: revenue effect, direct-cost effect,
indirect-cost effect, each as delta and as share of the EBITDA delta. Hand off to the
indirect-cost recipe when the indirect effect dominates.
