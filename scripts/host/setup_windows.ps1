# One-command setup on a fresh Windows 10/11 PC (PowerShell, run as Administrator).
#   1. installs Canonical Multipass with winget (uses Hyper-V, or VirtualBox on Home)
#   2. creates the "sdn-qos" Ubuntu 22.04 VM (4 vCPU, 4 GB RAM, 20 GB disk)
#   3. copies this repo into the VM (~/sdn-qos) and runs `make setup verify test`
# Usage (from the repo folder):
#   Set-ExecutionPolicy -Scope Process Bypass -Force
#   .\scripts\host\setup_windows.ps1
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$VM = "sdn-qos"

function Find-Multipass {
    $cmd = Get-Command multipass -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $p = "$env:ProgramFiles\Multipass\bin\multipass.exe"
    if (Test-Path $p) { return $p }
    return $null
}

$mp = Find-Multipass
if (-not $mp) {
    Write-Host "[win] installing Multipass via winget"
    winget install -e --id Canonical.Multipass --accept-source-agreements --accept-package-agreements
    Start-Sleep -Seconds 10
    $mp = Find-Multipass
    if (-not $mp) { throw "Multipass not found after install. Reboot, then re-run this script." }
}
Write-Host "[win] using $mp"

$exists = (& $mp list --format csv) -match "^$VM,"
if (-not $exists) {
    Write-Host "[win] creating VM $VM (downloads Ubuntu 22.04)"
    & $mp launch 22.04 --name $VM --cpus 4 --memory 4G --disk 20G
} else {
    & $mp start $VM
}

Write-Host "[win] copying repo into the VM"
& "$PSScriptRoot\sync_windows.ps1" -Direction push

Write-Host "[win] provisioning inside the VM (5-10 min the first time)"
& $mp exec $VM -- bash -lc "cd ~/sdn-qos && make setup verify test"
if ($LASTEXITCODE -ne 0) { throw "setup inside the VM failed (exit $LASTEXITCODE)" }
Write-Host ""
Write-Host "Ready. Run experiments with:  .\scripts\host\run_windows.ps1 run-all"
Write-Host "Or open a shell in the VM:      multipass shell $VM   (then: cd ~/sdn-qos)"
