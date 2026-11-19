param(
  [string]$PackageRoot = 'pyappify_dist/Monster-ww',
  [int]$TimeoutSeconds = 90
)

# Start the packaged app with the bundled Python against the packaged working
# tree, using an isolated PATH so developer-installed DLLs cannot mask a broken
# bundle. This is the gate that catches obfuscation defects -- a stale
# `from ok import ...` compiled into a .pyd crashes exactly here, before the
# window appears, just as it does on a real user's machine.

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $PackageRoot).Path
$pythonw = Join-Path $root 'data/apps/Monster-ww/python/pythonw.exe'
$python = Join-Path $root 'data/apps/Monster-ww/python/python.exe'
$working = Join-Path $root 'data/apps/Monster-ww/working'
$mainScript = Join-Path $working 'main.py'

if (-not (Test-Path -LiteralPath $pythonw)) { throw "Bundled pythonw.exe missing: $pythonw" }
if (-not (Test-Path -LiteralPath $mainScript)) { throw "Packaged main.py missing: $mainScript" }
$runner = if (Test-Path -LiteralPath $python) { $python } else { $pythonw }

$buildRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../build'))
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
$stdout = Join-Path $buildRoot 'portable-smoke.stdout.log'
$stderr = Join-Path $buildRoot 'portable-smoke.stderr.log'
Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue

$environmentNames = @('PATH', 'QT_QPA_PLATFORM', 'ONEDNN_MAX_CPU_ISA', 'OPENVINO_TELEMETRY_DISABLE')
$oldEnvironment = @{}
foreach ($name in $environmentNames) {
  $oldEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

$process = $null
try {
  # Deterministic pre-flight: import every module the framework loads at
  # startup. A compiled-in bad import fails here with a precise traceback,
  # faster and clearer than waiting for the GUI path.
  $preflight = @'
import importlib, re, pathlib, sys
sys.path.insert(0, r"{working}")
src = pathlib.Path(r"{working}/config.py").read_text(encoding="utf-8")
mods = set(re.findall(r"\[['\"](src\.[A-Za-z0-9_.]+)['\"]\s*,\s*['\"][A-Za-z0-9_]+['\"]\]", src))
failed = []
for m in sorted(mods):
    try:
        importlib.import_module(m)
    except Exception as e:
        failed.append((m, type(e).__name__, str(e)))
if failed:
    for m, t, msg in failed:
        print(f"IMPORT_FAILED {m}: {t}: {msg}")
    sys.exit(2)
print(f"PREFLIGHT_OK imported {len(mods)} registered modules")
'@ -replace '\{working\}', $working

  $probeOut = Join-Path $buildRoot 'portable-smoke.preflight.log'
  & $python -c $preflight *> $probeOut
  $probeText = Get-Content -LiteralPath $probeOut -Raw
  Write-Host $probeText
  if ($LASTEXITCODE -ne 0 -or $probeText -match 'IMPORT_FAILED') {
    throw "Packaged modules failed to import. See $probeOut"
  }

  # Isolated PATH + offscreen Qt keeps the smoke test independent of whatever
  # the runner has installed, and avoids opening a real window.
  $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
  $env:QT_QPA_PLATFORM = 'offscreen'
  $env:ONEDNN_MAX_CPU_ISA = 'AVX2'
  $env:OPENVINO_TELEMETRY_DISABLE = '1'

  $process = Start-Process -FilePath $runner `
    -ArgumentList "`"$mainScript`"" `
    -WorkingDirectory $working -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    Start-Sleep -Milliseconds 250
    $process.Refresh()
    [string]$errors = Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue
    [string]$output = Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue
    if ($errors -match 'Traceback \(most recent call last\)') {
      throw "Packaged app startup failed. See $stderr"
    }
    if ($process.HasExited) {
      throw "Packaged app exited during startup (code $($process.ExitCode)). See $stderr"
    }
    if ($output -match 'Window has fully displayed' -and $output -match 'default ocr init end') {
      Write-Host 'Portable smoke passed: main window shown and OCR initialized with an isolated PATH.'
      return
    }
  } while ((Get-Date) -lt $deadline)
  throw "Packaged app startup timed out after ${TimeoutSeconds}s. See $stdout and $stderr"
} finally {
  if ($process -and -not $process.HasExited) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    $process.WaitForExit()
  }
  foreach ($name in $environmentNames) {
    [Environment]::SetEnvironmentVariable($name, $oldEnvironment[$name], 'Process')
  }
}
