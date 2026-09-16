# Top movers: which members of a dimension explain a change (template)

Use for "why did <measure> go up/down" questions. Ranks the members of a dimension by their change
between two periods.

## 1. Rank by year-over-year change

```dax
EVALUATE
TOPN(
    15,
    CALCULATETABLE(
        SUMMARIZECOLUMNS(
            'Product'[Category],
            "This Year", [Revenue],
            "Last Year", [Revenue LY],
            "Change", [Revenue] - [Revenue LY]
        ),
        'Date'[Year] = 2026
    ),
    [Change], DESC
)
ORDER BY [Change] DESC
```

Swap 'Product'[Category] for the dimension the user cares about ('Store'[Region],
'Customer'[Customer Segment]). On the Inventory model use [Stock Value] with 'Warehouse'[Warehouse Name]
and compare two month-ends rather than years.
Run it twice (DESC and ASC) to show both the risers and the fallers.

## 2. Detail behind one mover

Hand off to the `detail-drilldown` recipe with the member as filter.

## 3. How to phrase the answer

Name the three largest movers with their change in the model's currency and as a share of the total
change, separate structural moves from one-offs, and state the period you compared.
