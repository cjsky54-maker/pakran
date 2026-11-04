param(
    [switch]$ReadMode
)

$ErrorActionPreference = "Stop"
if (-not [Environment]::Is64BitProcess) {
    throw "Run this diagnostic from 64-bit PowerShell."
}
if (-not (Get-Command Add-Type -ErrorAction SilentlyContinue)) {
    throw "Add-Type is unavailable in this PowerShell environment."
}

$source = Join-Path $PSScriptRoot "HikUvcXUDiagnostic.cs"
if (-not (Test-Path -LiteralPath $source)) {
    throw "Missing helper source: $source"
}
Add-Type -Path $source
$result = [HikUvcDiagnostic.Runner]::Run([bool]$ReadMode)
$result
