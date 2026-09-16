"""Exercise the actual build staging helper without Azure or private business files."""

import json
import shutil
import subprocess
import sys
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
    assert "powerbi_mcp/server.py" in staged["files"]
    expected_recipes = {
        f"skills/recipes/{p.name}" for p in (ROOT / "skills" / "recipes").iterdir() if p.suffix in (".md", ".yaml")
    }
    assert expected_recipes and expected_recipes <= set(staged["files"])
    assert {f"skills/models/{p.name}" for p in (ROOT / "skills" / "models").glob("*.md")} <= set(staged["files"])
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


def test_engine_layer_replaces_inherited_examples_and_stages_only_skills():
    script = f"""
    $ErrorActionPreference = 'Stop'
    . {ps_quote(ROOT / "deploy/build_context.ps1")}
    $context = New-GatewayBuildContext -AppRoot {ps_quote(ROOT)} -SkillsDir {ps_quote(ROOT / "skills")} -EngineImage 'registry/engine:v1'
    try {{
        if (Test-Path (Join-Path $context 'powerbi_mcp')) {{ throw 'Engine mode staged source' }}
        Get-Content -Raw -LiteralPath (Join-Path $context 'Dockerfile')
    }} finally {{ Remove-GatewayBuildContext $context }}
    """
    result = subprocess.run([PWSH, "-NoProfile", "-Command", script], capture_output=True, text=True, check=True)
    assert result.stdout.strip().splitlines() == [
        "FROM registry/engine:v1",
        "USER root",
        "RUN rm -rf /app/skills",
        "COPY --chown=gateway:gateway skills/ /app/skills/",
        "USER gateway",
    ]


def test_storage_mount_is_idempotent_preserves_other_settings_and_omits_secrets():
    script = f"""
    $ErrorActionPreference = 'Stop'
    . {ps_quote(ROOT / "deploy/configuration.ps1")}
    $spec = @{{properties=@{{configuration=@{{secrets=@(@{{name='jwt';value=$null}});ingress=@{{external=$true}}}};
        template=@{{volumes=@(@{{name='other'}});containers=@(@{{name='app';image='fixture:v1';
        env=@(@{{name='PBIMCP_JWT_SIGNING_KEY';secretRef='jwt'}});volumeMounts=@(@{{volumeName='other';mountPath='/other'}})}})}}}}}}
    $spec = Set-GatewayStorageMount $spec 'store'
    $spec = Set-GatewayStorageMount $spec 'store'
    $spec | ConvertTo-Json -Depth 20
    """
    output = subprocess.run([PWSH, "-NoProfile", "-Command", script], capture_output=True, text=True, check=True)
    props = json.loads(output.stdout)["properties"]
    assert "secrets" not in props["configuration"] and props["configuration"]["ingress"]["external"]
    assert len(props["template"]["volumes"]) == 2
    container = props["template"]["containers"][0]
    assert len(container["volumeMounts"]) == 2 and container["env"][0]["secretRef"] == "jwt"
    assert container["env"][1]["value"] == "/mnt/gateway-oauth"


def test_model_reconciliation_and_pinned_image_validation():
    script = f"""
    $ErrorActionPreference = 'Stop'
    . {ps_quote(ROOT / "deploy/configuration.ps1")}
    $current = @{{properties=@{{model=@{{name='model';version='v1'}}}};sku=@{{name='GlobalStandard';capacity=50}}}}
    if (Test-GatewayModelUpdate $current 'model' 'v1' 50) {{ throw 'Unchanged model updated' }}
    if (-not (Test-GatewayModelUpdate $current 'model' 'v2' 50)) {{ throw 'Version change ignored' }}
    if (-not (Test-GatewayModelUpdate $current 'model' 'v1' 100)) {{ throw 'Capacity change ignored' }}
    Assert-GatewayEngineImage 'registry/image:v1'
    Assert-GatewayEngineImage ''
    foreach ($invalid in @('registry/image:latest', 'registry/image', "registry/image:v1`nRUN bad")) {{
        $rejected = $false
        try {{ Assert-GatewayEngineImage $invalid }} catch {{ $rejected = $true }}
        if (-not $rejected) {{ throw 'Invalid engine image accepted' }}
    }}
    """
    subprocess.run([PWSH, "-NoProfile", "-Command", script], capture_output=True, text=True, check=True)


@pytest.mark.parametrize(
    "profile,success",
    [
        ({"DisableGeneration": True}, True),
        ({"FoundryEndpoint": "https://fixture.openai.azure.com"}, True),
        ({"FoundryEndpoint": "https://fixture.openai.azure.com", "DisableGeneration": True}, False),
        ({"FoundryResourceId": "/subscriptions/fixture/resourceGroups/fixture"}, False),
        ({"TypoSetting": "wrong"}, False),
        ({"TimeZone": "Invalid/Zone"}, False),
    ],
)
def test_profile_preflight_runs_without_azure(tmp_path, profile, success):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"AcrName": "fixtureacr", **profile}))
    # ValidateOnly exits before the Azure CLI is even resolved.
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(ROOT / "deploy/deploy_to_azure.ps1"),
            "-Profile",
            str(path),
            "-ValidateOnly",
            "-PythonExe",
            sys.executable,
        ],
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is success, result.stdout + result.stderr
    if success:
        assert "no Azure changes made" in result.stdout
