
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$composeFile = Join-Path $projectRoot 'deploy/stage_two/compose.yaml'
$credentialFile = Join-Path $projectRoot 'data/local/postgres.credential.xml'
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    foreach ($candidate in @('C:/Program Files/Docker/Docker/resources/bin/docker.exe', (Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/resources/bin/docker.exe'))) {
        if (Test-Path -LiteralPath $candidate) { $docker = Get-Item -LiteralPath $candidate; break }
    }
}
if (-not $docker) { throw 'Docker Desktop is not installed. Install and start it, then run this script again.' }
$dockerExe = if ($docker.Source) { $docker.Source } else { $docker.FullName }
$env:PATH = (Split-Path -Parent $dockerExe) + [IO.Path]::PathSeparator + $env:PATH
& $dockerExe info --format '{{.ServerVersion}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker engine is unavailable. Start Docker Desktop with the WSL 2 backend.' }
# Prefer the explicitly configured DSN. Capture credentials privately; never echo them.
$postgresConfigJson = & $python -c "import json,os,sys;sys.path.insert(0,'src');from insurance_rag.runtime_env import load_env;from psycopg.conninfo import conninfo_to_dict;from pathlib import Path;p=Path('.env');load_env(p) if p.exists() else None;dsn=os.environ.get('INSURANCE_POSTGRES_DSN','');c=conninfo_to_dict(dsn) if dsn else {};print(json.dumps({'dsn':dsn,'password':c.get('password','')}))"
if ($LASTEXITCODE -ne 0) { throw 'Invalid PostgreSQL environment configuration.' }
$postgresConfig = $postgresConfigJson | ConvertFrom-Json
$previousPostgresDsn = $env:INSURANCE_POSTGRES_DSN
$previousPostgresPassword = $env:INSURANCE_POSTGRES_PASSWORD
if ($postgresConfig.dsn) {
    $env:INSURANCE_POSTGRES_DSN = $postgresConfig.dsn
    $env:INSURANCE_POSTGRES_PASSWORD = $postgresConfig.password
} else {
    if (-not (Test-Path -LiteralPath $credentialFile)) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $credentialFile) | Out-Null
        $randomBytes = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($randomBytes) } finally { $rng.Dispose() }
        $secret = [Convert]::ToBase64String($randomBytes) | ConvertTo-SecureString -AsPlainText -Force
        [PSCredential]::new('insurance', $secret) | Export-Clixml -LiteralPath $credentialFile
    }
    $credential = Import-Clixml -LiteralPath $credentialFile
    $env:INSURANCE_POSTGRES_PASSWORD = $credential.GetNetworkCredential().Password
    $encodedPassword = [uri]::EscapeDataString($env:INSURANCE_POSTGRES_PASSWORD)
    $env:INSURANCE_POSTGRES_DSN = 'postgresql://insurance:' + $encodedPassword + '@127.0.0.1:5432/insurance_rag'
}
$env:INSURANCE_PROJECT_ROOT = $projectRoot
try {
    & $dockerExe compose -f $composeFile config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Invalid Compose configuration.' }
    & $dockerExe compose -f $composeFile up -d --wait --wait-timeout 240
    if ($LASTEXITCODE -ne 0) { throw 'Services failed to become healthy. Check docker compose logs.' }
    & $python -c "import os,psycopg; c=psycopg.connect(os.environ['INSURANCE_POSTGRES_DSN'],connect_timeout=10); print('PostgreSQL connection:',c.execute('SELECT 1').fetchone()[0]); c.close()"
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL connection failed. Check the configured DSN and the existing database credentials.' }
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:9200/_cluster/health' -TimeoutSec 15
    if ($health.status -eq 'red') { throw 'OpenSearch cluster is red.' }
    Write-Host ('OpenSearch health: ' + $health.status)
    Write-Host 'Current PostgreSQL and OpenSearch services are ready. Source indexes are prepared by the upload workflow.'

} finally {
    if ($null -eq $previousPostgresPassword) { Remove-Item Env:INSURANCE_POSTGRES_PASSWORD -ErrorAction SilentlyContinue } else { $env:INSURANCE_POSTGRES_PASSWORD = $previousPostgresPassword }
    if ($null -eq $previousPostgresDsn) { Remove-Item Env:INSURANCE_POSTGRES_DSN -ErrorAction SilentlyContinue } else { $env:INSURANCE_POSTGRES_DSN = $previousPostgresDsn }
}
