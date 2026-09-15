# Contoso Retail shared context (EXAMPLE)

This fictional deployment contains separate sales and inventory models. Choose the model before
applying business definitions. Use `get_business_context(model_id=...)` or `get_model_context`
to include its definitions; `analyze` does this automatically.

Use the model's calendar and date table. State the requested period, filters and assumptions.
Treat refresh time separately from query execution time: freshness is unknown unless a model
provides an explicit freshness query. An incomplete current period needs to be described as such.
