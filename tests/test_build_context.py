"""Exercise the actual build staging helper without Azure or private business files."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PWSH = shutil.which("pwsh") or ""
pytestmark = pytest.mark.skipif(not PWSH, reason="PowerShell 7 required for deployment helper tests")


def ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def test_build_upload_contains_only_runtime_and_selected_skills(tmp_path):
    private = tmp_path / "private"
    shutil.copytree(ROOT / "skills", private)
    (private / "glossary.md").write_text("private glossary fixture", encoding="utf-8")
    (private / ".env").write_text("SECRET=must-not-upload", encoding="utf-8")
    (private / "notes.txt").write_text("must-not-upload", encoding="utf-8")
    script = f"""
    $ErrorActionPreference = 'Stop'
    . {ps_quote(ROOT / "deploy" / "build_context.ps1")}
    $context = New-GatewayBuildContext -AppRoot {ps_quote(ROOT)} -SkillsDir {ps_quote(private)}
    try {{
        $files = @(Get-ChildItem -LiteralPath $context -Recurse -File | ForEach-Object {{ $_.FullName.Substring($context.Length + 1).Replace('\\', '/') }})
        @{{ files = $files; glossary = Get-Content -Raw -LiteralPath (Join-Path $context 'skills/glossary.md') }} | ConvertTo-Json
    }} finally {{ Remove-GatewayBuildContext $context }}
    if (Test-Path -LiteralPath $context) {{ throw 'Temporary context was not removed' }}
    """
    result = subprocess.run([PWSH, "-NoProfile", "-Command", script], capture_output=True, text=True, check=True)
    staged = json.loads(result.stdout)
    assert staged["glossary"] == "private glossary fixture"
    assert {"Dockerfile", "pyproject.toml", "uv.lock", "powerbi_mcp/server.py"} <= set(staged["files"])
    assert not any(name.startswith(("tests/", "deploy/", "scripts/", "docs/")) for name in staged["files"])
    expected_recipes = {f"skills/recipes/{p.name}" for p in (ROOT / "skills" / "recipes").glob("*.md")}
    assert expected_recipes and expected_recipes <= set(staged["files"])
    assert not any(".env" in name or "notes.txt" in name or ".git" in name for name in staged["files"])
    assert (private / ".env").exists()


def test_incomplete_skills_fail_before_build_and_cleanup_refuses_other_paths(tmp_path):
    script = f"""
    $ErrorActionPreference = 'Stop'
    . {ps_quote(ROOT / "deploy" / "build_context.ps1")}
    $rejected = $false
    try {{ New-GatewayBuildContext -AppRoot {ps_quote(ROOT)} -SkillsDir {ps_quote(tmp_path)} }}
    catch {{ $rejected = $_.Exception.Message -like '*missing instructions.md*' }}
    if (-not $rejected) {{ throw 'Incomplete skills accepted' }}
    $rejected = $false
    try {{ Remove-GatewayBuildContext {ps_quote(tmp_path)} }}
    catch {{ $rejected = $_.Exception.Message -like '*Refusing*' }}
    if (-not $rejected) {{ throw 'Unsafe cleanup accepted' }}
    """
    subprocess.run([PWSH, "-NoProfile", "-Command", script], capture_output=True, text=True, check=True)
    assert tmp_path.is_dir()


def test_example_profile_keys_are_all_script_parameters():
    """A profile key that is not a parameter makes the deploy script throw; keep the example honest."""
    script = f"""
    $params = ([System.Management.Automation.Language.Parser]::ParseFile({ps_quote(ROOT / "deploy" / "deploy_to_azure.ps1")}, [ref]$null, [ref]$null)).ParamBlock.Parameters |
        ForEach-Object {{ $_.Name.VariablePath.UserPath }}
    $params -join ','
    """
    result = subprocess.run([PWSH, "-NoProfile", "-Command", script], capture_output=True, text=True, check=True)
    parameters = set(result.stdout.strip().split(","))
    profile = json.loads((ROOT / "deploy" / "profiles" / "example.json").read_text(encoding="utf-8"))
    keys = {k for k in profile if not k.startswith("$")}
    assert keys <= parameters, f"profile keys without a parameter: {sorted(keys - parameters)}"
    assert {"AcrName", "SkillsDir", "EngineImage", "OwnerTag", "ReasoningEffort", "StorageName"} <= keys
