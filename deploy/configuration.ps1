# Pure configuration helpers: exercised without calling Azure.
function Assert-GatewayEngineImage([string]$EngineImage) {
    if ($EngineImage -and ($EngineImage -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._/:@-]+$' -or
        $EngineImage -notmatch '(:[a-zA-Z0-9._-]+|@sha256:[a-f0-9]{64})$' -or $EngineImage -match ':latest$')) {
        throw 'EngineImage must be a pinned image tag or sha256 digest, without whitespace; latest is not supported.'
    }
}

function Set-GatewayStorageMount([hashtable]$Specification, [string]$StorageName) {
    # Never submit exported secret placeholders: omission preserves the existing secret values.
    if ($Specification.properties.configuration) { $Specification.properties.configuration.Remove('secrets') }
    $template = $Specification.properties.template
    $template.volumes = @($template.volumes | Where-Object { $_ -and $_.name -ne 'gateway-oauth' }) +
        @(@{ name = 'gateway-oauth'; storageType = 'AzureFile'; storageName = $StorageName })
    $container = $template.containers[0]
    $container.volumeMounts = @($container.volumeMounts | Where-Object { $_ -and $_.volumeName -ne 'gateway-oauth' }) +
        @(@{ volumeName = 'gateway-oauth'; mountPath = '/mnt/gateway-oauth' })
    $container.env = @($container.env | Where-Object { $_.name -ne 'PBIMCP_OAUTH_STORAGE_DIR' }) +
        @(@{ name = 'PBIMCP_OAUTH_STORAGE_DIR'; value = '/mnt/gateway-oauth' })
    return $Specification
}

function Test-GatewayModelUpdate([object]$Current, [string]$ModelName, [string]$ModelVersion, [int]$ModelCapacity) {
    return (-not $Current -or $Current.properties.model.name -ne $ModelName -or
        $Current.properties.model.version -ne $ModelVersion -or $Current.sku.capacity -ne $ModelCapacity -or
        $Current.sku.name -ne 'GlobalStandard')
}
