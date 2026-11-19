param([int]$PerTestTimeoutSeconds = 90)

$Python = if (Test-Path ".\.venv\Scripts\python.exe") {
  ".\.venv\Scripts\python.exe"
} elseif (Test-Path ".\.venv\bin\python") {
  ".\.venv\bin\python"
} else {
  "python"
}

# Run each test module in its own process with a hard timeout. A single test
# whose framework leaves background threads running must not hang the whole
# build for 20 minutes -- kill it and surface a clear failure instead.
$failed = @()
Get-ChildItem -Path ".\tests\*.py" | ForEach-Object {
  $test = $_.FullName
  Write-Host "Running tests in $test"
  $log = Join-Path $env:TEMP ("ww_test_{0}.log" -f [guid]::NewGuid().ToString('N'))
  $proc = Start-Process -FilePath $Python `
    -ArgumentList @('-u', 'run_test.py', "`"$test`"") `
    -NoNewWindow -PassThru `
    -RedirectStandardOutput $log -RedirectStandardError "$log.err"
  $exited = $proc.WaitForExit($PerTestTimeoutSeconds * 1000)
  if (-not $exited) {
    Write-Error "TIMEOUT after ${PerTestTimeoutSeconds}s in $test"
    try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
    $failed += $test
  } else {
    Get-Content -LiteralPath $log -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
    Get-Content -LiteralPath "$log.err" -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
    if ($proc.ExitCode -ne 0) {
      Write-Error "FAILED (exit $($proc.ExitCode)) in $test"
      $failed += $test
    }
  }
  Remove-Item -LiteralPath $log, "$log.err" -Force -ErrorAction SilentlyContinue
}

if ($failed.Count -gt 0) {
  Write-Error "Failed test modules ($($failed.Count)): $($failed -join ', ')"
  exit 1
}
Write-Host 'All test modules passed.'
