# Compile (and optionally upload) the mosfet_scanner sketch.
#   .\build.ps1                  # compile only
#   .\build.ps1 -Upload          # compile + upload, auto-detect port
#   .\build.ps1 -Upload -Port COM5
param(
    [switch]$Upload,
    [string]$Port,
    [string]$Cli
)
$ErrorActionPreference = "Stop"

if (-not $Cli) {
    $found = Get-Command arduino-cli -ErrorAction SilentlyContinue
    if ($found) { $Cli = $found.Source }
    elseif (Test-Path "$env:LOCALAPPDATA\arduino-cli\arduino-cli.exe") {
        $Cli = "$env:LOCALAPPDATA\arduino-cli\arduino-cli.exe"
    }
    else {
        throw "arduino-cli not found. Install it or pass -Cli <path-to-arduino-cli.exe>."
    }
}

$sketch = Join-Path $PSScriptRoot "mosfet_scanner"

& $Cli compile --fqbn arduino:avr:uno $sketch --warnings all
if ($LASTEXITCODE -ne 0) { throw "compile failed" }

if ($Upload) {
    if (-not $Port) {
        $json = & $Cli board list --json | ConvertFrom-Json
        $ports = @($json.detected_ports | Where-Object { $_.port.protocol -eq "serial" })
        if ($ports.Count -eq 0) { throw "no serial ports found; pass -Port COMx" }
        # Prefer a port arduino-cli recognises as a board, else take the only/first one
        $match = $ports | Where-Object { $_.matching_boards } | Select-Object -First 1
        if (-not $match) { $match = $ports[0] }
        $Port = $match.port.address
        Write-Host "Auto-detected port: $Port"
    }
    & $Cli upload -p $Port --fqbn arduino:avr:uno $sketch
    if ($LASTEXITCODE -ne 0) { throw "upload failed" }
    Write-Host "Uploaded. The board prints its banner on the next serial connect."
}
