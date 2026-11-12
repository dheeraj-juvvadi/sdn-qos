# Copy the repo into the Multipass VM (push) or fetch results back (pull).
#   push : repo (minus .git and raw results) -> VM ~/sdn-qos
#   pull : VM experiments/results, docs/figures, docs/evidence -> repo
param([ValidateSet("push", "pull")][string]$Direction = "pull")
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$VM = "sdn-qos"
$mp = (Get-Command multipass -ErrorAction SilentlyContinue).Source
if (-not $mp) { $mp = "$env:ProgramFiles\Multipass\bin\multipass.exe" }
$tgz = Join-Path $env:TEMP "sdn-qos-sync.tgz"
if (Test-Path $tgz) { Remove-Item $tgz }

if ($Direction -eq "push") {
    # tar.exe ships with Windows 10 1803+ and Windows 11
    tar.exe -czf $tgz --exclude=.git --exclude=experiments/results/raw -C $Repo .
    & $mp transfer $tgz "${VM}:/tmp/sdn-qos-sync.tgz"
    & $mp exec $VM -- bash -lc "mkdir -p ~/sdn-qos && tar -xzf /tmp/sdn-qos-sync.tgz -C ~/sdn-qos"
} else {
    & $mp exec $VM -- bash -lc "cd ~/sdn-qos && sudo tar -czf /tmp/sdn-qos-out.tgz experiments/results docs/figures docs/evidence && sudo chmod 644 /tmp/sdn-qos-out.tgz"
    & $mp transfer "${VM}:/tmp/sdn-qos-out.tgz" $tgz
    tar.exe -xzf $tgz -C $Repo
}
Write-Host "[sync] $Direction done"
