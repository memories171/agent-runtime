$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
try {
  $p = [System.Diagnostics.Process]::GetProcessById(32188)
  $finished = $p.WaitForExit(1200000)
  if ($finished) {
    "STATUS=EXITED" | Out-File -Encoding utf8 b4_temporal_wait.out
    "EXITCODE=$($p.ExitCode)" | Out-File -Append -Encoding utf8 b4_temporal_wait.out
  } else {
    "STATUS=STILL_RUNNING_AFTER_20M" | Out-File -Encoding utf8 b4_temporal_wait.out
    "EXITCODE=UNAVAILABLE" | Out-File -Append -Encoding utf8 b4_temporal_wait.out
  }
} catch {
  "STATUS=PROCESS_NOT_FOUND" | Out-File -Encoding utf8 b4_temporal_wait.out
  "EXITCODE=UNAVAILABLE" | Out-File -Append -Encoding utf8 b4_temporal_wait.out
  "ERROR=$($_.Exception.Message)" | Out-File -Append -Encoding utf8 b4_temporal_wait.out
}
"---TEMPORAL-OUT---" | Out-File -Append -Encoding utf8 b4_temporal_wait.out
Get-Content "b4_temporal_it.out" -Encoding UTF8 | Out-File -Append -Encoding utf8 b4_temporal_wait.out
"---TEMPORAL-ERR---" | Out-File -Append -Encoding utf8 b4_temporal_wait.out
Get-Content "b4_temporal_it.err" -Encoding UTF8 | Out-File -Append -Encoding utf8 b4_temporal_wait.out
