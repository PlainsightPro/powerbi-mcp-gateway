---
name: Bug report
about: Something the gateway does wrong, so it can be reproduced and fixed
title: "[Bug]: "
labels: ["bug", "triage"]
assignees: []
---

## Summary

A clear, concise description of the bug.

## Steps to reproduce

1. ...
2. ...
3. ...

## Expected behaviour

What you expected to happen.

## Actual behaviour

What happened instead. The tool's `status` and `recovery` fields, or the error text the client
showed, are the most useful part.

## Environment

- Gateway version (engine image tag or commit):
- MCP client (Claude Desktop / claude.ai / Claude Code / VS Code / Cursor / ChatGPT / own code) and version:
- Deployment: (Azure Container Apps via the deploy script / local `python -m powerbi_mcp` / other)
- Which tool(s): (`analyze`, `generate_dax`, `execute_dax`, `list_semantic_models`, ...)

## Logs and evidence

<!-- Paste the relevant tool-call log lines from the container (they carry tool, user id, model id,
duration and outcome only). Redact tenant ids, workspace names and any business numbers. Never
paste tokens, client secrets or DAX that reveals your data model. -->

## Impact

- Severity: (blocker / high / medium / low)
- Does it concern permissions, row-level security or another user's data? (yes / no / unsure)
