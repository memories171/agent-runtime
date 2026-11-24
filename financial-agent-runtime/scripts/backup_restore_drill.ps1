# B5.2 backup/restore drill for the isolated local Staging Temporal SQL Persistence.
# Dumps the staging `temporal` database, restores it into an ISOLATED test database
# in the same staging Postgres instance, verifies key tables, records timing/size/
# checksum, then drops the test database. The running `temporal` DB is never touched.
param(
    [string] $PgContainer = "finora-staging-temporal-staging-postgres-1",
    [string] $PgUser = "temporal_staging",
    [string] $SourceDb = "temporal",
    [string] $TestDb = "temporal_restore_test",
    [string] $BackupDir = "backups"
)

$ErrorActionPreference = "Continue"
$script:Start = Get-Date
function Stamp { (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss.fffffffK") }

$backupFile = Join-Path (Join-Path (Get-Location) $BackupDir) "temporal_staging_$(Get-Date -Format 'yyyyMMdd_HHmmss').dump"
if (-not (Test-Path (Split-Path $backupFile))) { New-Item -ItemType Directory -Path (Split-Path $backupFile) -Force | Out-Null }

Write-Output "BACKUP_START=$(Stamp)"
Write-Output "PG_CONTAINER=$PgContainer SOURCE_DB=$SourceDb TEST_DB=$TestDb"

function Invoke-Pg([string[]] $PgArgs) {
    $out = & docker exec $PgContainer @PgArgs 2>&1
    $code = $LASTEXITCODE
    return [pscustomobject]@{ Output = ($out -join "`n"); ExitCode = $code }
}

# 1. Dump (custom format, compressed). Redirect container stdout to a host file.
Write-Output "--- pg_dump ---"
$dumpStart = Get-Date
$dumpProc = Start-Process -FilePath "docker" `
    -ArgumentList @("exec", $PgContainer, "pg_dump", "-U", $PgUser, "-Fc", "-d", $SourceDb) `
    -RedirectStandardOutput $backupFile -RedirectStandardError "$backupFile.err" `
    -NoNewWindow -Wait -PassThru
$dumpExit = $dumpProc.ExitCode
$dumpSec = [math]::Round(((Get-Date) - $dumpStart).TotalSeconds, 2)
Write-Output "DUMP_EXIT=$dumpExit DUMP_SECONDS=$dumpSec"
if ($dumpExit -ne 0) { Write-Error "pg_dump failed"; exit 1 }

$size = (Get-Item $backupFile).Length
$sha = (Get-FileHash $backupFile -Algorithm SHA256).Hash
Write-Output "BACKUP_FILE=$backupFile"
Write-Output "BACKUP_BYTES=$size"
Write-Output "BACKUP_SHA256=$sha"

# 2. Create isolated restore DB (drop first if a previous drill left it behind).
Write-Output "--- prepare isolated DB ---"
$drop = Invoke-Pg @("psql", "-U", $PgUser, "-d", "postgres", "-c", "DROP DATABASE IF EXISTS $TestDb;")
Write-Output "PREP_DROP_EXIT=$($drop.ExitCode)"
$create = Invoke-Pg @("psql", "-U", $PgUser, "-d", "postgres", "-c", "CREATE DATABASE $TestDb;")
Write-Output "PREP_CREATE_EXIT=$($create.ExitCode)"
if ($create.ExitCode -ne 0) { Write-Error "create test DB failed: $($create.Output)"; exit 1 }

# 3. Restore into the isolated DB (never the running SourceDb).
Write-Output "--- pg_restore ---"
$restoreStart = Get-Date
$restoreFile = Join-Path $BackupDir "temporal_staging_drill_restore.dump"
Copy-Item $backupFile -Force -Destination $restoreFile
& docker cp $restoreFile "${PgContainer}:/tmp/drill_restore.dump" 2>&1 | Out-Null
$restore = Invoke-Pg @("sh", "-c", "pg_restore -U $PgUser -d $TestDb --no-owner --no-privileges /tmp/drill_restore.dump 2>&1 | tail -5")
$restoreSec = [math]::Round(((Get-Date) - $restoreStart).TotalSeconds, 2)
Write-Output "RESTORE_EXIT=$($restore.ExitCode) RESTORE_SECONDS=$restoreSec"
Write-Output "RESTORE_OUTPUT=$($restore.Output)"
if ($restore.ExitCode -ne 0) { Write-Error "pg_restore failed"; exit 1 }
& docker exec $PgContainer rm -f /tmp/drill_restore.dump 2>&1 | Out-Null
Remove-Item $restoreFile -Force -ErrorAction SilentlyContinue

# 4. Verify key tables exist and compare row counts source vs restored.
#    The visibility DB must also be dumped/restored separately; it is a distinct database.
Write-Output "--- verify (main DB) ---"
$script:verifyOk = $true
function Compare-Count([string] $Table, [string] $Label) {
    $src = Invoke-Pg @("psql", "-U", $PgUser, "-d", $SourceDb, "-tAc", "SELECT count(*) FROM $Table;")
    $dst = Invoke-Pg @("psql", "-U", $PgUser, "-d", $TestDb, "-tAc", "SELECT count(*) FROM $Table;")
    $srcVal = $src.Output.Trim(); $dstVal = $dst.Output.Trim()
    $ok = ($src.ExitCode -eq 0 -and $dst.ExitCode -eq 0 -and $srcVal -match '^\d+$' -and $dstVal -match '^\d+$' -and $srcVal -eq $dstVal)
    if (-not $ok) { $script:verifyOk = $false }
    Write-Output "TABLE=$Label SOURCE_ROWS=$srcVal RESTORE_ROWS=$dstVal MATCH=$ok"
}
foreach ($t in @("namespaces", "executions", "history_node")) { Compare-Count $t $t }

# Restore + verify the visibility DB into its own isolated test DB.
$visSource = "temporal_visibility"
$visTest = "temporal_visibility_restore_test"
Write-Output "--- backup/restore visibility DB ---"
$visDrop = Invoke-Pg @("psql", "-U", $PgUser, "-d", "postgres", "-c", "DROP DATABASE IF EXISTS $visTest;")
$visCreate = Invoke-Pg @("psql", "-U", $PgUser, "-d", "postgres", "-c", "CREATE DATABASE $visTest;")
$visDump = Start-Process -FilePath "docker" `
    -ArgumentList @("exec", $PgContainer, "pg_dump", "-U", $PgUser, "-Fc", "-d", $visSource) `
    -RedirectStandardOutput "$backupFile.visibility" -RedirectStandardError "$backupFile.visibility.err" `
    -NoNewWindow -Wait -PassThru
Write-Output "VIS_DUMP_EXIT=$($visDump.ExitCode) VIS_BACKUP_BYTES=$((Get-Item "$backupFile.visibility").Length)"
Copy-Item "$backupFile.visibility" -Force -Destination "$BackupDir/temporal_visibility_drill_restore.dump"
& docker cp "$BackupDir/temporal_visibility_drill_restore.dump" "${PgContainer}:/tmp/vis_restore.dump" 2>&1 | Out-Null
$visRestore = Invoke-Pg @("sh", "-c", "pg_restore -U $PgUser -d $visTest --no-owner --no-privileges /tmp/vis_restore.dump 2>&1 | tail -5")
Write-Output "VIS_RESTORE_EXIT=$($visRestore.ExitCode)"
& docker exec $PgContainer rm -f /tmp/vis_restore.dump 2>&1 | Out-Null
Remove-Item "$BackupDir/temporal_visibility_drill_restore.dump" -Force -ErrorAction SilentlyContinue
$visSrcCount = Invoke-Pg @("psql", "-U", $PgUser, "-d", $visSource, "-tAc", "SELECT count(*) FROM executions_visibility;")
$visDstCount = Invoke-Pg @("psql", "-U", $PgUser, "-d", $visTest, "-tAc", "SELECT count(*) FROM executions_visibility;")
$visOk = ($visSrcCount.Output.Trim() -eq $visDstCount.Output.Trim() -and $visDstCount.Output.Trim() -match '^\d+$')
if (-not $visOk) { $verifyOk = $false }
Write-Output "TABLE=executions_visibility SOURCE_ROWS=$($visSrcCount.Output.Trim()) RESTORE_ROWS=$($visDstCount.Output.Trim()) MATCH=$visOk"

# namespace config blob check: retention is protobuf-encoded, so verify the
# default namespace exists with identical non-empty config bytes in both DBs.
Write-Output "--- verify namespace config integrity ---"
$nsSrc = Invoke-Pg @("psql", "-U", $PgUser, "-d", $SourceDb, "-tAc", "SELECT md5(data) FROM namespaces WHERE name='default';")
$nsDst = Invoke-Pg @("psql", "-U", $PgUser, "-d", $TestDb, "-tAc", "SELECT md5(data) FROM namespaces WHERE name='default';")
$nsSrcHash = $nsSrc.Output.Trim(); $nsDstHash = $nsDst.Output.Trim()
$nsOk = ($nsSrc.ExitCode -eq 0 -and $nsDst.ExitCode -eq 0 -and $nsSrcHash -match '^[0-9a-f]{32}$' -and $nsSrcHash -eq $nsDstHash)
if (-not $nsOk) { $verifyOk = $false }
Write-Output "NAMESPACE_DEFAULT_CONFIG_MD5_SOURCE=$nsSrcHash RESTORE=$nsDstHash MATCH=$nsOk"

# 5. Cleanup isolated test DBs (running DBs untouched).
Write-Output "--- cleanup isolated DBs ---"
$cleanup1 = Invoke-Pg @("psql", "-U", $PgUser, "-d", "postgres", "-c", "DROP DATABASE IF EXISTS $TestDb;")
$cleanup2 = Invoke-Pg @("psql", "-U", $PgUser, "-d", "postgres", "-c", "DROP DATABASE IF EXISTS $visTest;")
Write-Output "TEST_DB_DROPPED=$($cleanup1.ExitCode -eq 0) VIS_TEST_DB_DROPPED=$($cleanup2.ExitCode -eq 0)"
Remove-Item "$backupFile.err", "$backupFile.visibility.err" -Force -ErrorAction SilentlyContinue

Write-Output "VERIFY_OK=$verifyOk"
Write-Output "BACKUP_END=$(Stamp)"
Write-Output "ELAPSED_SECONDS=$([math]::Round(((Get-Date) - $script:Start).TotalSeconds, 2))"
Write-Output "RESULT=$([bool]($verifyOk -and $dumpExit -eq 0 -and $restore.ExitCode -eq 0 -and $visDump.ExitCode -eq 0 -and $visRestore.ExitCode -eq 0 -and $nsOk))"
