param()
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$dsn = "postgresql+asyncpg://agent_app:agent_app@host.docker.internal:5433/agent_runtime"
$migrationDsn = "postgresql://agent:agent@host.docker.internal:5433/agent_runtime"
$start = Get-Date
& docker exec `
  -e "DATABASE_URL=$dsn" `
  -e "TEST_DATABASE_URL=$dsn" `
  -e "TEST_MIGRATION_DSN=$migrationDsn" `
  b4-stable-test `
  pytest -q -m "not temporal_it and not wall_clock_30m" `
  1> b4_linux_full.out 2> b4_linux_full.err
$code = $LASTEXITCODE
$end = Get-Date
$meta = @(
  "START=$($start.ToString('o'))",
  "END=$($end.ToString('o'))",
  "ELAPSED=$($end - $start)",
  "EXITCODE=$code",
  "STDOUT=b4_linux_full.out",
  "STDERR=b4_linux_full.err",
  'COMMAND=docker exec ... pytest -q -m "not temporal_it and not wall_clock_30m"'
)
$meta | Out-File -Encoding utf8 b4_linux_meta.out
"EXITCODE=$code" | Out-File -Append -Encoding utf8 b4_linux_full.out
exit $code
