param(
  [Parameter(Mandatory = $true)]
  [string]$LogPrefix,
  [string]$MarkerExpression = "",
  [string[]]$PytestArgs = @(),
  [switch]$Temporal,
  [switch]$WallClock
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if ($Temporal) {
  $env:RUN_TEMPORAL_IT = "1"
}
if ($WallClock) {
  $env:RUN_WALL_CLOCK_30M = "1"
  $env:RUN_TEMPORAL_IT = "1"
}

$stdout = "$LogPrefix.out"
$stderr = "$LogPrefix.err"
$meta = "$LogPrefix.meta.out"
$args = @("-q", "--tb=short") + $PytestArgs
if ($MarkerExpression) {
  $args += @("-m", $MarkerExpression)
}

$start = Get-Date
$cmd = "python -m pytest " + ($args -join " ")
$previousEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& python -m pytest @args 1> $stdout 2> $stderr
$code = $LASTEXITCODE
$ErrorActionPreference = $previousEap
$end = Get-Date

$lines = @(
  "START=$($start.ToString('o'))",
  "END=$($end.ToString('o'))",
  "ELAPSED=$($end - $start)",
  "EXITCODE=$code",
  "COMMAND=$cmd",
  "MARKER=$MarkerExpression",
  "TEMPORAL_SWITCH=$([bool]$Temporal)",
  "WALL_CLOCK_SWITCH=$([bool]$WallClock)",
  "STDOUT=$stdout",
  "STDERR=$stderr"
)
$lines | Out-File -Encoding utf8 $meta
"EXITCODE=$code" | Out-File -Append -Encoding utf8 $stdout
exit $code
