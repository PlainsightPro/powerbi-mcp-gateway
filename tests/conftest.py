import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"


@pytest.fixture
def skills_dir() -> Path:
    return SKILLS


@pytest.fixture
def dummy_env(monkeypatch):
    monkeypatch.setenv("PBIMCP_TENANT_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("PBIMCP_CLIENT_ID", "00000000-0000-0000-0000-000000000002")
    monkeypatch.setenv("PBIMCP_CLIENT_SECRET", "not-a-real-secret")
    monkeypatch.setenv("PBIMCP_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("PBIMCP_JWT_SIGNING_KEY", "0123456789abcdef0123456789abcdef")
    monkeypatch.delenv("PBIMCP_FOUNDRY_ENDPOINT", raising=False)
    # never pick up a developer's .env during tests
    monkeypatch.chdir(ROOT / "tests")
    return os.environ
