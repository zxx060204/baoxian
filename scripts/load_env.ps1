# Dot-source from PowerShell: . .\scripts\load_env.ps1
# Loads literal KEY=VALUE pairs into this process; never evaluates file content.
[CmdletBinding()]
param([string]$Path = (Join-Path (Split-Path $PSScriptRoot -Parent) '.env'))

$resolvedEnvPath = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
$envLines = [System.IO.File]::ReadAllLines($resolvedEnvPath, [System.Text.Encoding]::UTF8)
$envEntries = @{}
$lineNumber = 0
foreach ($envLine in $envLines) {
    $lineNumber++
    $envTrimmed = $envLine.Trim()
    if (-not $envTrimmed -or $envTrimmed.StartsWith('#')) { continue }
    if ($envTrimmed -notmatch '^([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
        throw "Invalid environment assignment at line $lineNumber."
    }
    $envKey = $Matches[1]
    $envValue = $Matches[2].Trim()
    if ($envValue.Length -ge 2 -and (
        ($envValue.StartsWith('"') -and $envValue.EndsWith('"')) -or
        ($envValue.StartsWith("'") -and $envValue.EndsWith("'"))
    )) { $envValue = $envValue.Substring(1, $envValue.Length - 2) }
    # Blank template placeholders do not erase existing local service settings.
    if ($envValue.Length -eq 0) { continue }
    if ($envEntries.ContainsKey($envKey)) { throw "Duplicate environment key at line $lineNumber." }
    $envEntries[$envKey] = $envValue
}
foreach ($envKey in $envEntries.Keys) {
    [System.Environment]::SetEnvironmentVariable($envKey, $envEntries[$envKey], 'Process')
}
Write-Host "Environment loaded for this PowerShell session. Values are not displayed."
