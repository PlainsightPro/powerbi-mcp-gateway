<#
.SYNOPSIS
    Deploys the Power BI MCP Gateway as its own Azure application.

.DESCRIPTION
    Creates or updates, idempotently:
      1. Entra app registration "Power BI MCP Server" (confidential client for the OAuth proxy and OBO):
         exposed API scope access_as_user, access-token version 2, delegated Power BI permissions with
         admin consent, redirect URIs, client secret.
      2. Foundry resource (AIServices, project management on) with a Foundry project and a gpt-5
         Global Standard deployment.
      3. Container registry + image build (az acr build, no local Docker needed).
      4. Log Analytics + Container Apps environment + the container app (system-assigned identity,
         min 1 replica: the OAuth proxy keeps its token store on local disk).
      5. Role "Cognitive Services OpenAI User" for the app identity on the Foundry resource.

    Run from anywhere; paths resolve relative to this script. Re-run after code changes to build and
    roll a new image. Secrets never hit the console: the client secret goes straight into a Container
    App secret, and with -WriteLocalEnv also into the git-ignored .env next to the app.

    One codebase, many deployments: every parameter can come from a JSON deployment profile
    (-Profile), so a customer or internal deployment is a private folder with skills/ and
    deploy/profile.json and nothing else. With -EngineImage the build does not compile this source
    at all; it layers the skills on the published engine image (ghcr.io/plainsightpro/powerbi-mcp-gateway:<tag>),
    which is how deployments upgrade: bump the tag, redeploy. Explicit arguments always win over
    the profile.

.EXAMPLE
    .\deploy_to_azure.ps1 -AcrName <yourUniqueRegistry>                                   # example skills, source build
    .\deploy_to_azure.ps1 -Profile C:\deployments\contoso\deploy\profile.json             # skills + names from the profile
    .\deploy_to_azure.ps1 -Profile ...\profile.json -EngineImage ghcr.io/plainsightpro/powerbi-mcp-gateway:0.2.0
    .\deploy_to_azure.ps1 -Profile ...\profile.json -SkipFoundry -SkipBuild               # config-only update
    .\deploy_to_azure.ps1 -AcrName <yourUniqueRegistry> -PreauthorizeAzureCli -WriteLocalEnv
#>
[CmdletBinding()]
param(
    [string]$Profile = "",        # JSON file whose keys are these parameter names (see deploy/profiles/example.json)
    [string]$EngineImage = "",    # published engine image to layer the skills on; empty = build this source
    [string]$Location = "swedencentral",
    [string]$ResourceGroup = "rg-powerbi-mcp",
    [string]$AppName = "ca-powerbi-mcp",
    [string]$EnvName = "env-powerbi-mcp",
    [string]$LogName = "log-powerbi-mcp",
    [string]$AcrName = "",        # globally unique, 5-50 alphanumerics; required here or in the profile
    [string]$FoundryName = "aif-powerbi-mcp",
    [string]$FoundryProject = "powerbi-mcp",
    [string]$ModelName = "gpt-5",
    [string]$ModelVersion = "2025-08-07",
    [int]$ModelCapacity = 50,
    [string]$EntraAppName = "Power BI MCP Server",
    [string]$SkillsDir = "", # defaults to the public example skills; pass the private folder for production
    [string]$ServerName = "Power BI MCP Gateway",
    [string]$ImageTag = (Get-Date -Format "yyyyMMdd-HHmm"),
    [string]$OwnerTag = "",   # Azure Policy requires tag 'Owner' on resource groups; defaults to the signed-in user
    [switch]$SkipFoundry,
    [switch]$SkipBuild,
    [switch]$RotateSecret,
    [switch]$PreauthorizeAzureCli,
    [switch]$WriteLocalEnv
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"   # az CLI streams build logs through cp1252 on Windows and dies on Unicode otherwise
$appRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'build_context.ps1')

if ($Profile) {
    # A deployment profile supplies defaults; anything passed explicitly on the command line wins.
    $profilePath = (Resolve-Path -LiteralPath $Profile -ErrorAction Stop).Path
    $profileDir = Split-Path $profilePath -Parent
    $profileSettings = Get-Content -LiteralPath $profilePath -Raw | ConvertFrom-Json
    foreach ($setting in $profileSettings.PSObject.Properties) {
        if ($setting.Name -like '$*' -or $PSBoundParameters.ContainsKey($setting.Name)) { continue }
        if (-not (Get-Variable -Name $setting.Name -Scope Script -ErrorAction SilentlyContinue)) {
            throw "Profile '$profilePath' sets unknown parameter '$($setting.Name)'"
        }
        $value = $setting.Value
        if ($setting.Name -eq 'SkillsDir' -and $value -and -not [IO.Path]::IsPathRooted($value)) {
            $value = Join-Path $profileDir $value   # relative to the profile, so the folder is self-contained
        }
        Set-Variable -Name $setting.Name -Value $value -Scope Script
    }
    Write-Host "Deployment profile: $profilePath"
}
if ($AcrName -notmatch '^[a-zA-Z0-9]{5,50}$') {
    throw "-AcrName (5-50 alphanumerics, globally unique) is required, on the command line or in the profile"
}
if (-not $SkillsDir) { $SkillsDir = Join-Path $appRoot 'skills' }
# Validate before any Azure mutations, even for configuration-only deployments.
$SkillsDir = (Resolve-Path -LiteralPath $SkillsDir -ErrorAction Stop).Path
foreach ($requiredSkill in @('instructions.md', 'glossary.md', 'dax-rules.md', 'catalog.yaml')) {
    if (-not (Test-Path -LiteralPath (Join-Path $SkillsDir $requiredSkill) -PathType Leaf)) {
        throw "Skills folder is missing $requiredSkill : $SkillsDir"
    }
}
$script:AzCli = (Get-Command az -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source   # never the helper below

$PowerBiApiAppId = "00000009-0000-0000-c000-000000000000"
$GraphAppId = "00000003-0000-0000-c000-000000000000"
$AzureCliAppId = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
$PowerBiScopes = @(
    "7f33e027-4039-419b-938e-2f8ca153e68e=Scope",  # Dataset.Read.All
    "b2f1b2fa-f35c-407c-979c-a858a808ba85=Scope",  # Workspace.Read.All
    "6b03f425-0a8e-4c54-ba35-df73806f1396=Scope",  # MLModel.Execute.All
    "4ae1bf56-f562-4747-b7bc-2fa0874ed46f=Scope"   # Report.Read.All
)
$GraphUserRead = "e1fe6dd8-ba31-4d61-89e7-88639da4683d=Scope"

function Step([string]$Message) { Write-Host "`n== $Message" -ForegroundColor Cyan }
function Invoke-Az {
    # Runs az, fails the script on a non-zero exit, returns stdout as a single string.
    $out = & $script:AzCli @args 2>&1
    # Arguments/output may contain credentials. Never include them in exceptions.
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI operation '$($args[0]) $($args[1])' failed (exit $LASTEXITCODE)." }
    return ($out | Where-Object { $_ -is [string] }) -join "`n"
}
function Invoke-AzQuiet {
    # Same as Invoke-Az but tolerates failure (returns $null), for existence checks.
    $out = & $script:AzCli @args 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return ($out | Where-Object { $_ -is [string] }) -join "`n"
}
function NewRandomHex([int]$Bytes = 32) {
    $buffer = New-Object byte[] $Bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($buffer)
    return ($buffer | ForEach-Object { $_.ToString("x2") }) -join ""
}

$subscription = Invoke-Az account show --query id -o tsv
$tenant = Invoke-Az account show --query tenantId -o tsv
if (-not $OwnerTag) { $OwnerTag = Invoke-Az account show --query user.name -o tsv }
Write-Host "Subscription $subscription, tenant $tenant, location $Location, owner tag $OwnerTag"

# ---------------------------------------------------------------- 1. resource group
Step "Resource group $ResourceGroup"
Invoke-Az group create -n $ResourceGroup -l $Location --tags "Owner=$OwnerTag" "Application=powerbi-mcp" -o none | Out-Null

# ---------------------------------------------------------------- 2. Entra app registration
Step "Entra app registration '$EntraAppName'"
$appId = Invoke-AzQuiet ad app list --display-name $EntraAppName --query "[0].appId" -o tsv
if (-not $appId) {
    $appId = Invoke-Az ad app create --display-name $EntraAppName --sign-in-audience AzureADMyOrg --query appId -o tsv
    Write-Host "created app $appId"
    Start-Sleep -Seconds 8   # let the new object replicate before we PATCH it
    Invoke-Az ad app update --id $appId --identifier-uris "api://$appId" | Out-Null
} else {
    Write-Host "app exists: $appId"
}
$objectId = Invoke-Az ad app show --id $appId --query id -o tsv

$scopeId = Invoke-AzQuiet ad app show --id $appId --query "api.oauth2PermissionScopes[?value=='access_as_user'].id | [0]" -o tsv
if (-not $scopeId) { $scopeId = [guid]::NewGuid().ToString() }
$api = @{
    requestedAccessTokenVersion = 2
    oauth2PermissionScopes = @(@{
        id = $scopeId; value = "access_as_user"; type = "User"; isEnabled = $true
        adminConsentDisplayName = "Access Power BI MCP as the signed-in user"
        adminConsentDescription = "Allows the Power BI MCP server to query Power BI semantic models on behalf of the signed-in user."
        userConsentDisplayName = "Access Power BI MCP on your behalf"
        userConsentDescription = "Allows the Power BI MCP server to query Power BI semantic models you can access."
    })
}
function Patch-Application([hashtable]$Body) {
    $bodyFile = New-TemporaryFile
    ($Body | ConvertTo-Json -Depth 8) | Set-Content -Path $bodyFile -Encoding utf8
    try {
        Invoke-Az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/applications/$objectId" `
            --headers "Content-Type=application/json" --body "@$bodyFile" | Out-Null
    } finally { Remove-Item $bodyFile -Force }
}
Patch-Application @{ api = $api }
Write-Host "exposed scope api://$appId/access_as_user (token v2)"
if ($PreauthorizeAzureCli) {
    # Graph validates pre-authorizations against scopes that already exist, so this is a second call.
    Patch-Application @{ api = @{ preAuthorizedApplications = @(@{ appId = $AzureCliAppId; delegatedPermissionIds = @($scopeId) }) } }
    Write-Host "Azure CLI pre-authorized on that scope"
}

# Merge and deduplicate: permission add appends duplicates on each deployment.
$permissions = @{}
$existingPermissions = Invoke-Az ad app show --id $appId --query requiredResourceAccess -o json | ConvertFrom-Json
foreach ($resource in $existingPermissions) {
    if (-not $permissions.ContainsKey($resource.resourceAppId)) { $permissions[$resource.resourceAppId] = @{} }
    foreach ($permission in $resource.resourceAccess) {
        $permissions[$resource.resourceAppId]["$($permission.id):$($permission.type)"] = @{ id = $permission.id; type = $permission.type }
    }
}
foreach ($requiredApi in @(@{ id = $PowerBiApiAppId; scopes = $PowerBiScopes }, @{ id = $GraphAppId; scopes = @($GraphUserRead) })) {
    if (-not $permissions.ContainsKey($requiredApi.id)) { $permissions[$requiredApi.id] = @{} }
    foreach ($scope in $requiredApi.scopes) {
        $parts = $scope.Split('=')
        $permissions[$requiredApi.id]["$($parts[0]):$($parts[1])"] = @{ id = $parts[0]; type = $parts[1] }
    }
}
Patch-Application @{ requiredResourceAccess = @(
    foreach ($apiId in ($permissions.Keys | Sort-Object)) {
        @{ resourceAppId = $apiId; resourceAccess = @($permissions[$apiId].Values) }
    }
) }
if (-not (Invoke-AzQuiet ad sp show --id $appId --query id -o tsv)) { Invoke-Az ad sp create --id $appId | Out-Null; Start-Sleep -Seconds 15 }
$consented = $false
foreach ($attempt in 1..5) {
    # Right after the service principal is created, the consent API can still race it
    # ("service principal name is already present"); replication settles within a minute.
    & az ad app permission admin-consent --id $appId 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $consented = $true; break }
    Start-Sleep -Seconds 15
}
if (-not $consented) { throw "admin consent for $appId kept failing; grant it in the Entra portal and re-run" }
Write-Host "delegated Power BI permissions admin-consented"

$appExists = [bool](Invoke-AzQuiet containerapp show -n $AppName -g $ResourceGroup --query name -o tsv)
$clientSecret = $null
if ($RotateSecret -or -not $appExists) {
    $clientSecret = Invoke-Az ad app credential reset --id $appId --display-name $AppName --years 2 --append --query password -o tsv
    Write-Host "client secret issued (2 years)"
}

# ---------------------------------------------------------------- 3. Foundry + gpt-5
if (-not $SkipFoundry) {
    Step "Foundry resource $FoundryName"
    if (-not (Invoke-AzQuiet cognitiveservices account show -n $FoundryName -g $ResourceGroup --query name -o tsv)) {
        Invoke-Az cognitiveservices account create -n $FoundryName -g $ResourceGroup -l $Location --kind AIServices --sku S0 `
            --custom-domain $FoundryName --assign-identity --allow-project-management true --yes -o none | Out-Null
        Write-Host "created"
    } else { Write-Host "exists" }

    $projectUri = "https://management.azure.com/subscriptions/$subscription/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts/$FoundryName/projects/$FoundryProject" + "?api-version=2025-06-01"
    $projectFile = New-TemporaryFile   # JSON through az.cmd loses its quotes; a file body survives
    (@{ location = $Location; identity = @{ type = "SystemAssigned" }
        properties = @{ displayName = $FoundryProject; description = "Power BI MCP Gateway: DAX generation" } } |
        ConvertTo-Json -Depth 5) | Set-Content -Path $projectFile -Encoding utf8
    $projectOut = & az rest --method PUT --uri $projectUri --headers "Content-Type=application/json" --body "@$projectFile" 2>&1
    Remove-Item $projectFile -Force
    if ($LASTEXITCODE -eq 0) { Write-Host "project $FoundryProject ensured" }
    else { Write-Warning "Foundry project not created (the model deployment works without it): $projectOut" }

    $deployed = Invoke-AzQuiet cognitiveservices account deployment show -n $FoundryName -g $ResourceGroup --deployment-name $ModelName --query name -o tsv
    if (-not $deployed) {
        Invoke-Az cognitiveservices account deployment create -n $FoundryName -g $ResourceGroup --deployment-name $ModelName `
            --model-name $ModelName --model-version $ModelVersion --model-format OpenAI `
            --sku-name GlobalStandard --sku-capacity $ModelCapacity -o none | Out-Null
        Write-Host "deployment $ModelName ($ModelVersion, GlobalStandard, $ModelCapacity K TPM) created"
    } else { Write-Host "deployment $ModelName exists" }
}
$foundryEndpoint = Invoke-AzQuiet cognitiveservices account show -n $FoundryName -g $ResourceGroup --query "properties.endpoints.\"OpenAI Language Model Instance API\"" -o tsv
if (-not $foundryEndpoint) { $foundryEndpoint = "https://$FoundryName.openai.azure.com/" }
$foundryEndpoint = $foundryEndpoint.TrimEnd("/")

# ---------------------------------------------------------------- 4. registry + image
Step "Container registry $AcrName"
if (-not (Invoke-AzQuiet acr show -n $AcrName --query name -o tsv)) {
    Invoke-Az acr create -n $AcrName -g $ResourceGroup --sku Basic --admin-enabled true -o none | Out-Null
}
$acrServer = Invoke-Az acr show -n $AcrName --query loginServer -o tsv
$image = "$acrServer/powerbi-mcp:$ImageTag"
if (-not $SkipBuild) {
    Step "Building $image (cloud build$(if ($EngineImage) { ", skills on $EngineImage" } else { ', from source' }))"
    # --no-logs: az.cmd runs Python in isolated mode, so the cp1252 log streamer cannot be switched to
    # UTF-8 and crashes on the first non-ASCII byte in the build output. Queue, then poll the run.
    $buildContext = New-GatewayBuildContext -AppRoot $appRoot -SkillsDir $SkillsDir -EngineImage $EngineImage
    try {
        $queued = & $script:AzCli acr build --registry $AcrName --image "powerbi-mcp:$ImageTag" --image "powerbi-mcp:latest" `
            --file (Join-Path $buildContext "Dockerfile") --no-logs $buildContext 2>&1 | Out-String
        $buildExit = $LASTEXITCODE
    } finally {
        Remove-GatewayBuildContext $buildContext
    }
    if ($buildExit -ne 0) { throw "acr build failed:`n$queued" }
    if ($queued -notmatch "ID:\s*(\w+)") { throw "acr build was not queued:`n$queued" }
    $runId = $Matches[1]
    $status = ""
    foreach ($i in 1..90) {   # up to 15 minutes
        Start-Sleep -Seconds 10
        $status = Invoke-AzQuiet acr task show-run --registry $AcrName --run-id $runId --query status -o tsv
        if ($status -in @("Succeeded", "Failed", "Canceled", "Error", "Timeout")) { break }
    }
    if ($status -ne "Succeeded") { throw "acr build run $runId ended with status '$status' (az acr task logs -r $AcrName --run-id $runId)" }
    Write-Host "build $runId succeeded"
} else {
    if (-not $appExists) { throw '-SkipBuild requires an existing container app.' }
    $image = Invoke-Az containerapp show -n $AppName -g $ResourceGroup --query 'properties.template.containers[0].image' -o tsv
}
$acrUser = Invoke-Az acr credential show -n $AcrName --query username -o tsv
$acrPass = Invoke-Az acr credential show -n $AcrName --query "passwords[0].value" -o tsv

# ---------------------------------------------------------------- 5. environment + container app
Step "Log Analytics + Container Apps environment"
Invoke-Az monitor log-analytics workspace create -g $ResourceGroup -n $LogName -l $Location -o none | Out-Null
$lawId = Invoke-Az monitor log-analytics workspace show -g $ResourceGroup -n $LogName --query customerId -o tsv
$lawKey = Invoke-Az monitor log-analytics workspace get-shared-keys -g $ResourceGroup -n $LogName --query primarySharedKey -o tsv
if (-not (Invoke-AzQuiet containerapp env show -n $EnvName -g $ResourceGroup --query name -o tsv)) {
    Invoke-Az containerapp env create -n $EnvName -g $ResourceGroup -l $Location --logs-workspace-id $lawId --logs-workspace-key $lawKey -o none | Out-Null
}

Step "Container app $AppName"
$envVars = @(
    "PBIMCP_TENANT_ID=$tenant",
    "PBIMCP_CLIENT_ID=$appId",
    "PBIMCP_SERVER_NAME=$ServerName",
    "PBIMCP_SKILLS_DIR=/app/skills",
    "PBIMCP_CLIENT_SECRET=secretref:entra-client-secret",
    "PBIMCP_JWT_SIGNING_KEY=secretref:jwt-signing-key",
    "PBIMCP_FOUNDRY_ENDPOINT=$foundryEndpoint",
    "PBIMCP_FOUNDRY_DEPLOYMENT=$ModelName",
    "PBIMCP_FOUNDRY_REASONING_EFFORT=low"
)
if (-not $appExists) {
    $jwtKey = NewRandomHex 32
    Invoke-Az containerapp create -n $AppName -g $ResourceGroup --environment $EnvName --image $image `
        --registry-server $acrServer --registry-username $acrUser --registry-password $acrPass `
        --system-assigned --ingress external --target-port 8000 --transport auto `
        --min-replicas 1 --max-replicas 1 --cpu 0.5 --memory 1.0Gi `
        --secrets "entra-client-secret=$clientSecret" "jwt-signing-key=$jwtKey" `
        --env-vars @envVars "PBIMCP_BASE_URL=https://pending" -o none | Out-Null
    Write-Host "created"
} else {
    if ($clientSecret) { Invoke-Az containerapp secret set -n $AppName -g $ResourceGroup --secrets "entra-client-secret=$clientSecret" -o none | Out-Null }
    Invoke-Az containerapp update -n $AppName -g $ResourceGroup --image $image --set-env-vars @envVars -o none | Out-Null
    Write-Host "updated to $image"
}
$fqdn = Invoke-Az containerapp show -n $AppName -g $ResourceGroup --query properties.configuration.ingress.fqdn -o tsv
$baseUrl = "https://$fqdn"
Invoke-Az containerapp update -n $AppName -g $ResourceGroup --set-env-vars "PBIMCP_BASE_URL=$baseUrl" -o none | Out-Null

Step "Role: app identity -> Cognitive Services OpenAI User on $FoundryName"
$principalId = Invoke-Az containerapp show -n $AppName -g $ResourceGroup --query identity.principalId -o tsv
$foundryId = Invoke-Az cognitiveservices account show -n $FoundryName -g $ResourceGroup --query id -o tsv
$assigned = $false
foreach ($attempt in 1..4) {   # a brand-new identity can take a moment to replicate
    & az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal `
        --role "Cognitive Services OpenAI User" --scope $foundryId -o none 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $assigned = $true; break }
    Start-Sleep -Seconds 15
}
if ($assigned) { Write-Host "role assigned" } else { Write-Warning "role assignment failed; generate_dax will get 401 from Foundry until it is granted" }

Step "Redirect URIs on the Entra app"
Invoke-Az ad app update --id $appId --web-redirect-uris "$baseUrl/auth/callback" "http://localhost:8000/auth/callback" | Out-Null

# ---------------------------------------------------------------- summary
Write-Host ""
Write-Host "Power BI MCP server deployed" -ForegroundColor Green
Write-Host "  MCP endpoint : $baseUrl/mcp"
Write-Host "  Health       : $baseUrl/healthz"
Write-Host "  Entra app    : $EntraAppName ($appId)"
Write-Host "  Foundry      : $foundryEndpoint  deployment $ModelName"
Write-Host "  Image        : $image$(if ($EngineImage) { "  (engine $EngineImage + skills from $SkillsDir)" })"
Write-Host ""
Write-Host "Claude Code   : claude mcp add --transport http powerbi $baseUrl/mcp"
Write-Host "Claude Desktop: Settings > Connectors > Add custom connector > URL $baseUrl/mcp (no client id needed)"
Write-Host "VS Code       : add {""type"":""http"",""url"":""$baseUrl/mcp""} to mcp.json"
if ($WriteLocalEnv) {
    $envPath = Join-Path $appRoot ".env"
    $existingSecret = $null
    if (Test-Path $envPath) {
        $existingSecret = (Get-Content $envPath | Where-Object { $_ -like "PBIMCP_CLIENT_SECRET=*" } | Select-Object -First 1) -replace "^PBIMCP_CLIENT_SECRET=", ""
    }
    $secretForEnv = if ($clientSecret) { $clientSecret } else { $existingSecret }
    @(
        "PBIMCP_TENANT_ID=$tenant",
        "PBIMCP_CLIENT_ID=$appId",
        "PBIMCP_CLIENT_SECRET=$secretForEnv",
        "PBIMCP_BASE_URL=http://localhost:8000",
        "PBIMCP_JWT_SIGNING_KEY=$(NewRandomHex 16)",
        "PBIMCP_FOUNDRY_ENDPOINT=$foundryEndpoint",
        "PBIMCP_FOUNDRY_DEPLOYMENT=$ModelName",
        "PBIMCP_FOUNDRY_REASONING_EFFORT=low"
        "PBIMCP_SKILLS_DIR=$SkillsDir"
        "PBIMCP_SERVER_NAME=$ServerName"
    ) | Set-Content -Path $envPath -Encoding utf8
    Write-Host ""
    Write-Host "Local .env written to $envPath (git-ignored)$(if (-not $secretForEnv) { ' - no client secret available; re-run with -RotateSecret' })"
}
