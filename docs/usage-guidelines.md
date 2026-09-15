# Usage guidelines

The gateway lets your assistant query Power BI using your own permissions and row-level security.
Queries are read-only. Business definitions live in the deployment's skills folder.

## Your first session

1. Open the gateway's welcome page, connect your assistant and sign in.
2. Ask “Which Power BI models can I query?” Query access can be checked separately from discovery.
3. Ask a question with a model name, or paste a Power BI report link.
4. Include the period and scope. Check any clarification or assumptions before relying on the answer.

For example: “Show revenue by month for the last six complete months in the sales model.”
Follow with “Break that down by category.” The assistant can pass the returned context to retain
the period and filters. When changing scope, say so explicitly.

## Check the answer

- Check execution status first. A prepared query, clarification request or failed step is not a result.
- Ask for the model, date table, period, filters and DAX used.
- Compare a figure with a trusted report under the same filters. Personal report slicers are not
  visible to the gateway; describe them in the question.
- Inspect row limits and completeness. A limited table may omit groups needed for a total;
  ask for an aggregate query instead of adding a partial table.
- Check known freshness. Query execution time does not say when the model's source data last changed.

Use a named recipe for repeated analyses. Executable recipes need explicit typed parameters
and stop when a step fails. The assistant should explain partial results before drawing conclusions.

## Privacy and access

Questions and metadata go to the configured Foundry deployment when server generation is enabled;
result rows return to your assistant. With client-side generation, the assistant receives model
context and writes DAX itself. Use an assistant approved for the data you work with.

The gateway's analysis logs record request id, model id, status, duration and attempt count.
They do not intentionally record queries, tokens or result rows. Lower-level framework logging
can contain error details; restrict access to operational logs.

Skills are deployment-wide. Model-specific selection improves relevance but is not a permission
boundary for the glossary or recipes. Separate deployments when groups must not share business knowledge.

See [workflows](workflows.md) for tool details and [troubleshooting](troubleshooting.md) for recovery.
