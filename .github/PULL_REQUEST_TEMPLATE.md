<!-- Keep the summary short and the checklist honest. Draft PRs are welcome for early feedback. -->

## Summary

<!-- What changes, and why? Link the issue it closes (Closes #123). -->

## Type of change

- [ ] Bug fix
- [ ] New feature or tool behaviour
- [ ] Deploy script / infrastructure
- [ ] Docs only
- [ ] CI / build / dependencies

## Checklist

- [ ] **Tests** added or updated and green locally: `uv run pytest -q`. Tests stay offline
      (`httpx.MockTransport`, the stub Responses client, in-memory storage).
- [ ] **Lint and types** clean: `uv run ruff check . && uv run ruff format --check . && uv run pyright`.
- [ ] **No organisation vocabulary** in `powerbi_mcp/` or `tests/`: tool names stay generic and
      fixtures use invented names; anything domain-specific lives in a skills folder.
- [ ] **Every Power BI call still runs as the signed-in user.** No service-principal or shared
      identity path was added.
- [ ] **Docs** follow the code: a line under *Unreleased* in `CHANGELOG.md`, and `docs/`,
      `README.md` or `AGENTS.md` updated when users, administrators or contributors would notice.
- [ ] **Secrets and logs**: nothing sensitive committed; new log lines carry no questions, DAX,
      result rows or tokens.
- [ ] **Deploy script** changes keep it idempotent and were run with `-ValidateOnly`; profile keys
      match parameter names (`deploy/profiles/example.json`).

## Notes for reviewers

<!-- Risky areas, follow-ups, manual verification steps (for example the result of
scripts/check_gateway.py against a test deployment). -->
