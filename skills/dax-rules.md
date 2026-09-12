- Exactly one EVALUATE per query. A `DEFINE MEASURE` block before it is allowed; `DEFINE TABLE`
  and `DEFINE COLUMN` are not. Up to four queries per execute call.
- Reference tables as 'Table Name', columns as 'Table Name'[Column], measures as [Measure].
  The quotes wrap the table name only; nothing goes between `[` and `]` except the exact
  column or measure name (`[Year Month Id]`, never `[Year Month Id']`).
- Prefer SUMMARIZECOLUMNS with the model's own measures:
  `SUMMARIZECOLUMNS('Dim'[Col], FILTER(VALUES('Dim'[Col]), <condition>), "Label", [Measure])`.
  Never re-aggregate a fact column when a measure already covers it.
- Filter time through the date table, not the fact: use 'Date'[Year Month Id] (integer yyyymm) for
  month ranges and 'Date'[Year] for years.
- Year-over-year comparisons: put one year in filter context and use the model's This Year /
  Last Year / Growth measures instead of building DATEADD logic.
- Rankings: `TOPN(n, SUMMARIZECOLUMNS(...), [Alias], DESC)`; always add `ORDER BY` to the outer query.
  Keep result sets small (the default cap is 250 rows); aggregate before you list.
- Percentages and ratios come back as fractions; do not multiply by 100 in DAX.
- Do not round or format in DAX; the client formats.
- When the question is ambiguous (which year, which entity), pick the most likely reading, state it
  in `assumptions`, and keep the query simple rather than clever.
- Output only the JSON object {dax, explanation, assumptions}.
