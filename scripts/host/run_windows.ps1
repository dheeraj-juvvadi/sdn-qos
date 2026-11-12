# Run a Makefile target inside the Multipass VM, then copy results back.
#   .\scripts\host\run_windows.ps1 run-all     (full reproduction, ~45 min)
#   .\scripts\host\run_windows.ps1 smoke
param([string]$Target = "run-all")
$ErrorActionPreference = "Stop"
$mp = (Get-Command multipass -ErrorAction SilentlyContinue).Source
if (-not $mp) { $mp = "$env:ProgramFiles\Multipass\bin\multipass.exe" }
& "$PSScriptRoot\sync_windows.ps1" -Direction push      # pick up local edits
& $mp exec sdn-qos -- bash -lc "cd ~/sdn-qos && make $Target"
$rc = $LASTEXITCODE
& "$PSScriptRoot\sync_windows.ps1" -Direction pull
exit $rc
