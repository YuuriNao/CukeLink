param(
    [Parameter(Mandatory = $true)]
    [string]$NebulaCert,

    [Parameter(Mandatory = $false)]
    [string]$OutDir = "runtime\\pki",

    [Parameter(Mandatory = $false)]
    [string]$NetworkCidr = "192.168.100.0/24",

    [Parameter(Mandatory = $true)]
    [string]$LighthouseIp,

    [Parameter(Mandatory = $false)]
    [string[]]$ClientIps = @()
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $NebulaCert)) {
    throw "nebula-cert not found at $NebulaCert"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$caName = "p2p-lan-ca"
& $NebulaCert ca -name $caName

Move-Item -Force -Path "ca.crt" -Destination (Join-Path $OutDir "ca.crt")
Move-Item -Force -Path "ca.key" -Destination (Join-Path $OutDir "ca.key")

& $NebulaCert sign -name "lighthouse" -ip "$LighthouseIp"
Move-Item -Force -Path "lighthouse.crt" -Destination (Join-Path $OutDir "lighthouse.crt")
Move-Item -Force -Path "lighthouse.key" -Destination (Join-Path $OutDir "lighthouse.key")

for ($i = 0; $i -lt $ClientIps.Count; $i++) {
    $ip = $ClientIps[$i]
    $name = "client$($i + 1)"
    & $NebulaCert sign -name $name -ip "$ip"
    Move-Item -Force -Path "$name.crt" -Destination (Join-Path $OutDir "$name.crt")
    Move-Item -Force -Path "$name.key" -Destination (Join-Path $OutDir "$name.key")
}

Write-Host "Certificates written to $OutDir"
