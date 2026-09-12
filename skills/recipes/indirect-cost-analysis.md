# Recipe: why are indirect costs growing?

Typical question: "Why are our indirect costs growing so fast, and what does it do to EBITDA?"
Model: Finance. Run the queries in order with `execute_dax`; each step narrows the previous one.
Replace the year-month bounds with the window the user asked for (default: the last 12 full months).

## 1. Monthly trend of revenue, indirect cost and EBITDA

```dax
EVALUATE
SUMMARIZECOLUMNS(
    'Date'[Year Month Id],
    FILTER(VALUES('Date'[Year Month Id]), 'Date'[Year Month Id] >= 202509 && 'Date'[Year Month Id] <= 202608),
    "Revenue", [Revenue],
    "Indirect Cost", [Indirect Cost],
    "Indirect Cost % Revenue", [Indirect Cost % Revenue],
    "EBITDA", [EBITDA]
)
ORDER BY 'Date'[Year Month Id]
```

Read it as: is the cost level rising, or is revenue falling while cost stays flat? Flag incomplete months.

## 2. Which GL accounts drive the growth (this year vs last year)

```dax
EVALUATE
TOPN(
    15,
    CALCULATETABLE(
        SUMMARIZECOLUMNS(
            'GL Account'[GL Account Code],
            'GL Account'[GL Account Name],
            "This Year", [Indirect Cost This Year],
            "Last Year", [Indirect Cost Last Year],
            "Growth", [Indirect Cost Growth]
        ),
        'Date'[Year] = 2026
    ),
    [Growth], DESC
)
ORDER BY [Growth] DESC
```

## 3. Same view by management cost category

```dax
EVALUATE
CALCULATETABLE(
    SUMMARIZECOLUMNS('Cost Category'[Cost Category Name], "Amount", [Financial Breakdown]),
    'Date'[Year] = 2026
)
ORDER BY [Amount]
```

## 4. Transaction detail behind one driver

```dax
EVALUATE
TOPN(
    50,
    CALCULATETABLE(
        SUMMARIZECOLUMNS(
            'Transactions'[Transaction Date],
            'Transactions'[Counterparty],
            'Transactions'[Description],
            "Amount", [Transaction Amount]
        ),
        'GL Account'[GL Account Code] = "613000",
        'Date'[Year] = 2026
    ),
    [Amount], ASC
)
ORDER BY [Amount] ASC
```

Costs are negative, so ASC puts the largest cost first.

## 5. How to phrase the answer

Lead with the size of the move (per month and as % of revenue, then vs last year), name the top
three drivers with their growth, separate structural growth from one-offs, and tie it back to EBITDA
versus a revenue or direct-cost effect. Actions are the user's call; offer the levers the data supports.
