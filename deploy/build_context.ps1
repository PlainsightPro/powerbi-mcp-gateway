# Explicit allowlist: a build must never upload .env, Git history, or other local files.
#
# Two build modes share this staging step:
#   source build   (default)      Dockerfile + requirements + powerbi_mcp/ + the selected skills
#   engine image   (-EngineImage) a two-line Dockerfile: FROM <published engine image>, COPY skills
# The second mode is how customer deployments track the public engine without a fork: they keep
# only a skills folder and a deployment profile, and bump the image tag to upgrade.
function New-GatewayBuildContext([string]$AppRoot, [string]$SkillsDir, [string]$EngineImage = "") {
    $skillRoot = (Resolve-Path -LiteralPath $SkillsDir -ErrorAction Stop).Path
    $required = @('instructions.md', 'glossary.md', 'dax-rules.md', 'catalog.yaml')
    foreach ($name in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $skillRoot $name) -PathType Leaf)) {
            throw "Skills folder is missing $name : $skillRoot"
        }
    }
    $context = Join-Path ([IO.Path]::GetTempPath()) ('pbimcp-build-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $context | Out-Null
    try {
        if ($EngineImage) {
            @("FROM $EngineImage", "COPY skills/ /app/skills/") | Set-Content -Path (Join-Path $context 'Dockerfile') -Encoding ascii
        } else {
            foreach ($name in @('Dockerfile', 'requirements.txt')) {
                Copy-Item -LiteralPath (Join-Path $AppRoot $name) -Destination $context
            }
            $codeTarget = New-Item -ItemType Directory -Path (Join-Path $context 'powerbi_mcp')
            Get-ChildItem -LiteralPath (Join-Path $AppRoot 'powerbi_mcp') -File -Filter '*.py' |
                Copy-Item -Destination $codeTarget.FullName
        }
        $skillsTarget = New-Item -ItemType Directory -Path (Join-Path $context 'skills')
        foreach ($name in $required) {
            Copy-Item -LiteralPath (Join-Path $skillRoot $name) -Destination $skillsTarget.FullName
        }
        $recipes = Join-Path $skillRoot 'recipes'
        if (Test-Path -LiteralPath $recipes -PathType Container) {
            $recipeTarget = New-Item -ItemType Directory -Path (Join-Path $skillsTarget.FullName 'recipes')
            Get-ChildItem -LiteralPath $recipes -File -Filter '*.md' | Copy-Item -Destination $recipeTarget.FullName
        }
        return $context
    } catch {
        Remove-GatewayBuildContext $context
        throw
    }
}

function Remove-GatewayBuildContext([string]$Context) {
    $resolved = (Resolve-Path -LiteralPath $Context -ErrorAction Stop).Path
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)
    if ((Split-Path $resolved -Parent) -ne $tempRoot -or
        (Split-Path $resolved -Leaf) -notmatch '^pbimcp-build-[a-f0-9]{32}$') {
        throw "Refusing to remove a path outside a gateway temporary build directory."
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
