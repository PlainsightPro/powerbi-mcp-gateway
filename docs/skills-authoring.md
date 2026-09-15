# Authoring skills

A private skills folder contains the business knowledge for one deployment. The engine is generic.
Start with `python -m powerbi_mcp init C:\deployments\contoso`, or adapt the fictional examples.
Initialization refuses a nonempty destination. Optional `--gateway https://<host>/mcp` imports
accessible model metadata through browser sign-in; `--catalog-from models.json` imports a saved list.

| File | Purpose |
|---|---|
| `instructions.md` | Short instructions for choosing tools, handling statuses and explaining results |
| `glossary.md` | Definitions shared across models |
| `models/*.md` | Model-specific vocabulary, measure meanings, date conventions and known traps |
| `dax-rules.md` | Shared query rules |
| `recipes/*.md` | Recurring analysis instructions and interpretation guidance |
| `recipes/*.yaml` | Optional executable recipes with scalar parameters and ordered query steps |
| `catalog.yaml` | Model discovery context, aliases, topics and links to model definitions |

Skills load at startup. Restart or redeploy after editing. Their contents are available to all users
of the deployment; model scoping is about relevance, not access control.

## Catalog and model context

```yaml
models:
  - id: 11111111-1111-1111-1111-111111111111
    name: Contoso Sales
    workspace: Sales Analytics
    aliases: [sales, turnover]
    topics: [revenue, margin]
    description: Invoiced sales by product and store.
    data_scope: All countries; filter the store country for one market.
    default_date_table: Date
    calendar: Calendar year; full months end on the last calendar day.
    key_measures: [Revenue]
    context_file: models/sales.md
    recipes: [monthly-trend]
    notes: Returns are included in the measure.
```

`workspace_id` is optional. Use exact object names. An optional `freshness_query` is a single,
read-only DAX query returning the model's documented data-as-of value; it runs separately after a
generated analysis, adding a request. Without it, freshness is unknown. Never substitute the
current time for source freshness.

Put definitions such as measure formulas, sign conventions, active date relationships and
ambiguous business terms in the selected model file. Shared glossary text is always included.
Document columns and measures in the semantic model too: descriptions, relationships, format
strings and native metadata are retained.

## Executable recipes

Keep interpretation guidance in Markdown. Add the same-named YAML file to make the recipe
executable. YAML-only recipes also get prompts and resources. See the complete fictional example
in [monthly-trend.yaml](../skills/recipes/monthly-trend.yaml).

```yaml
version: 1
title: Constant probe
description: Small executable example without business objects.
parameters:
  value:
    type: integer
    description: Value to return.
    default: 1
steps:
  - id: probe
    dax: 'EVALUATE ROW("Value", {{value}})'
    max_rows: 1
    min_rows: 1
```

Types are `string`, `integer`, `number`, `date` (ISO YYYY-MM-DD), and `boolean`. Optional
`choices` restrict values. A missing value uses `default`; otherwise it is required.
An optional parameter (`required: false`) must have a default. Placeholders represent complete,
escaped DAX values: never put them inside quotes, identifiers or comments. Dynamic table or
measure names are deliberately unsupported; create a separate validated query for each choice.

Optional `models` restricts the recipe to known model ids. `required_objects` entries have
`table`, `name`, and `kind` (`column` or `measure`). All references are checked against the
selected user's schema before execution. Catalog references to incompatible recipes are rejected.

Recipes have 1–12 steps with unique ids. `depends_on` can reference preceding steps only.
Steps run in file order, stop on failure and preserve previous results. Optional `expected_columns`
and `min_rows` check responses. Column names must match the hosted response exactly, including
brackets where present. Dependencies enforce order; they do not substitute previous result values.

## Validate before deployment

```powershell
python -m powerbi_mcp validate C:\deployments\contoso\skills
python -m powerbi_mcp validate C:\deployments\contoso\skills --schemas schemas.json
python -m powerbi_mcp validate C:\deployments\contoso\skills --gateway https://<host>/mcp
.\deploy\deploy_to_azure.ps1 -Profile C:\deployments\contoso\deploy\profile.json -ValidateOnly
```

Offline validation checks file shape, unknown catalog keys, duplicates, recipe references,
parameter types, placeholders and query structure. A schema snapshot file maps model ids to full
schema objects. Snapshot or live validation additionally checks key measures, date tables and
recipe objects. These checks do not prove numerical correctness or DAX engine acceptance.

## Check expected answers

Keep a private JSON list of regression cases outside the staged skills files:

```json
[
  {
    "name": "constant probe",
    "model_id": "11111111-1111-1111-1111-111111111111",
    "dax": "EVALUATE ROW(\"Value\", 1)",
    "expect": {
      "min_rows": 1,
      "max_rows": 1,
      "columns": ["[Value]"],
      "scalars": [{"column": "[Value]", "value": 1, "tolerance": 0}]
    }
  }
]
```

Adjust column names to the actual hosted response. Each case supplies exactly one of `dax`,
`question`, or `recipe`. Question cases require `context.reference_date`; include period,
timezone and filters for reproducibility. Recipe cases also name the result `step` to check.

```powershell
python -m powerbi_mcp evaluate --gateway https://<host>/mcp --cases cases.json --output evaluation-results.json
```

This executes up to 50 cases as the signed-in user; question cases incur generation usage.
Checks cover execution status, row bounds, expected columns and scalar values with absolute
tolerance. `expect.require_complete=true` requires explicit upstream completeness.
The summary reports case names and failures without queries or business rows. Compare known
figures under stable scope and rerun after model, recipe or generation changes.
