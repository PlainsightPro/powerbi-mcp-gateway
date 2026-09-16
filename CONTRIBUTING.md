# Contributing

Thanks for helping. The short version: keep the engine free of any organisation's vocabulary, keep
the tests offline, and run the same checks CI runs before you push.

## Setup

```powershell
uv sync                                                  # .venv from uv.lock, dev tools included
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run pytest -q
uv run python -m powerbi_mcp --check-skills skills
```

`AGENTS.md` describes the layout and the design rules; `docs/private-skills.md` explains why the
example skills are fictional and how real deployments are kept out of this repository.

Optional but recommended, the pre-commit hooks run ruff and a few sanity checks (merge markers,
private keys, large files) on every commit:

```powershell
uvx pre-commit install
```

CI runs the same lint, type and test commands on Python 3.11 and 3.14, builds the image and probes
`/healthz`, and scans the whole git history for secrets with gitleaks (`.gitleaks.toml`); a
committed credential fails the build. This is a public repository: never push a real tenant id,
client secret, workspace name or business number, not even in a test fixture.

## Rules of thumb

- **No domain terms in `powerbi_mcp/` or `tests/`.** Tool names are generic; prompts, glossary
  entries and recipes come from the skills folder. Test fixtures use invented names.
- **Tests do not touch the network.** Upstreams are `httpx.MockTransport`, the model is a stub
  Responses client, storage is the in-memory key-value store. `tests/test_gateway.py` is the place
  for behaviour; `tests/test_server.py` asserts the MCP surface.
- **Every Power BI call runs as the user.** Do not add a service-principal path.
- **Docs describe what the code does.** A behaviour change comes with its line in `CHANGELOG.md`
  under *Unreleased* and, when users or administrators would notice, a doc update.

## Releasing

1. Move the *Unreleased* section of `CHANGELOG.md` under the new version.
2. Set `__version__` in `powerbi_mcp/__init__.py`.
3. Tag `vX.Y.Z` and push the tag. The release workflow refuses a tag that does not match the
   version, publishes the image and creates the GitHub Release.
