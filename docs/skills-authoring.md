# Authoring skills

A skills folder is what turns the generic gateway into *your* assistant. It is plain Markdown and
YAML, loaded once when the server starts, and it is the only place domain knowledge lives: the
engine has no idea what "revenue" or "indirect cost" means. Start from the fictional example in
`skills/` and replace every line.

| File | Reaches the assistant as | Write it for |
|---|---|---|
| `instructions.md` | Server instructions, sent to every client at connection time | The assistant. How to work with the tools, what to always do first, house rules |
| `glossary.md` | `get_business_context`, `skill://glossary`; also fed to the DAX generator with every question | A reader who has never seen the models: which measure answers which question, sign conventions, which date table to use, traps |
| `dax-rules.md` | The DAX generator's system prompt; `skill://dax-rules` | The generator: query style, filtering rules, what not to do |
| `recipes/<name>.md` | `get_recipe`, one MCP prompt per file, `skill://recipes/<name>` | The assistant: a tested sequence of queries for one recurring question, with how to read the results |
| `catalog.yaml` | `list_semantic_models` (curated rows), notes injected into the schema and the generator | The assistant: what each model is for, its scope, date table, key measures, recipes |

## instructions.md

Keep it under a page. State the working method (list models first, read the context, prefer
`generate_dax` with execute, use recipes), the rules (currency, sign conventions, never call
Microsoft's GenerateQuery, how to handle access errors, answer language) and anything specific to
your organisation ("two Finance models exist; never add their numbers").

## glossary.md

The most valuable file. Structure that works:

1. **Key measures** as a table: business term, measure name in brackets, meaning and sign.
2. **Which table answers what**: fact tables and their drill-down columns, dimensions and their
   useful columns, the date table(s) and the integer year-month column.
3. **Vocabulary the business uses**: the phrases people say, mapped to measures and filters.
4. **Traps**: inactive relationships, many-to-many bridges, duplicate labels across pipelines,
   frozen or legacy tables, incomplete current month.

Use the exact names from the model; the generator copies them. Descriptions written in the model
itself (TMDL `///` comments) also reach the schema tool, so document measures there too.

## dax-rules.md

House rules as a bullet list. The example covers the essentials (one EVALUATE, quote tables, prefer
measures over re-aggregation, filter via the date table, keep results small). Add rules for your
model's conventions: which date table, how fiscal years work, which selector tables must be set.

## recipes

One file per recurring analysis. The file name becomes the recipe and prompt name (lower-case,
hyphens: `stock-aging.md`). Structure:

- A `#` heading: the title the assistant sees in the index.
- The typical question and the model it applies to.
- Numbered steps, each with a complete DAX query in a ```dax fence and one or two lines on how to
  read the result and what to adapt (period bounds, filters).
- How to phrase the answer.

Validate every query against the model before you ship the recipe; the assistant follows recipes
before it improvises, so a wrong recipe produces confidently wrong answers.

## catalog.yaml

```yaml
models:
  - id: <semantic model id from app.powerbi.com/groups/<workspace>/datasets/<id>>
    name: Finance
    workspace: Finance
    description: >
      What this model is for, in one or two sentences.
    data_scope: Which entities or markets it covers; how to filter to one.
    default_date_table: Date
    key_measures: [Revenue, Gross Margin, Units]
    recipes: [monthly-trend, top-movers]
    notes: |
      Free text injected with the schema and into the generator: legacy tables to avoid,
      incomplete periods, selector tables.
```

Models the user can open but that are not in the catalog are still listed, marked as uncurated.

## Testing a skills folder

- `PBIMCP_SKILLS_DIR=<folder>` in `.env`, then `python -m powerbi_mcp` and connect a client; or
  `scripts/smoke_test.py "<a question>"` for a headless run including DAX generation.
- The deploy script refuses a folder that lacks any of the four required files.
- Keep a few question/expected-answer pairs per recipe and re-run them after model changes.
