# Detail drill-down: the rows behind one number (template)

Use when the user wants to see the transactions that make up a figure. Always filter to one member
and one period first; never list a fact table unfiltered.

```dax
EVALUATE
TOPN(
    50,
    CALCULATETABLE(
        SUMMARIZECOLUMNS(
            'Sales'[Invoice Number],
            'Sales'[Invoice Date],
            'Product'[Product Name],
            "Revenue", [Revenue]
        ),
        'Product'[Category] = "Outdoor",
        'Date'[Year Month Id] = 202606
    ),
    [Revenue], DESC
)
ORDER BY [Revenue] DESC
```

Replace the two filters with the member and period from the previous step. Keep the row cap; ask the
user before going deeper than 50 rows.
