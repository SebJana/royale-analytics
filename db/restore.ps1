# PowerShell restore script for MongoDB
param(
    [Parameter(Mandatory=$true)]
    [string]$DumpDir
)

# Check if dump directory exists
if (-not (Test-Path $DumpDir -PathType Container)) {
    Write-Host "Usage: .\restore.ps1 .\backups\clash_royale_YYYY-MM-DD_HH-MM-SS"
    Write-Host "Error: Directory '$DumpDir' does not exist"
    exit 1
}

# Load environment variables from parent .env file
$envFile = "..\\.env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^([^#][^=]*?)=(.*)$') {
            Set-Variable -Name $matches[1] -Value $matches[2] -Scope Script
        }
    }
}

$MONGO_HOST = if ($MONGO_HOST) { $MONGO_HOST } else { "mongo" }
$MONGO_PORT = if ($MONGO_PORT) { $MONGO_PORT } else { "27017" }
$MONGO_DB = if ($MONGO_APP_DB) { $MONGO_APP_DB } else { "clash_royale" }
$MONGO_USER = if ($MONGO_APP_USER) { $MONGO_APP_USER } else { "data_scraper" }
$MONGO_PWD = if ($MONGO_APP_PWD) { $MONGO_APP_PWD } else { "secret" }
$MONGO_AUTH_DB = if ($MONGO_APP_DB) { $MONGO_APP_DB } else { "clash_royale" }

# Get absolute path for Docker volume mount
$AbsoluteDumpDir = (Resolve-Path $DumpDir).Path

# Backups are written with mongodump --gzip. A dump without compressed files is
# refused here, before mongorestore drops any collection.
if (-not (Get-ChildItem -LiteralPath $AbsoluteDumpDir -Recurse -File -Filter '*.bson.gz' |
    Select-Object -First 1)) {
    Write-Host "[restore] error: $DumpDir contains no compressed (*.bson.gz) dump"
    exit 1
}

Write-Host "[restore] restoring $DumpDir -> $MONGO_DB on ${MONGO_HOST}:${MONGO_PORT}"

# Run mongorestore using Docker
docker run --rm `
  --network "clash-royale-analytics_cr-analytics" `
  -v "${AbsoluteDumpDir}:/dump:ro" `
  mongo:7.0 mongorestore `
    --host $MONGO_HOST `
    --port $MONGO_PORT `
    --username $MONGO_USER `
    --password $MONGO_PWD `
    --authenticationDatabase $MONGO_AUTH_DB `
    --nsInclude="${MONGO_DB}.*" `
    --drop `
    --gzip `
    /dump

if ($LASTEXITCODE -eq 0) {
    Write-Host "[restore] done."
} else {
    Write-Host "[restore] failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}
