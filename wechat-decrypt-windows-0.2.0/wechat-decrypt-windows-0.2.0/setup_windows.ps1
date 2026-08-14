$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Installing Windows dependencies..."
python -m pip install --upgrade -r (Join-Path $ProjectRoot "requirements-windows.txt")

Write-Host ""
Write-Host "Installation complete."
Write-Host "First run / automatic:   .\windows.ps1 auto --account <wxid_directory>"
Write-Host "Check/reuse a saved key: .\windows.ps1 status --account <wxid_directory>"
Write-Host "Capture a new key:       .\windows.ps1 extract --account <wxid_directory>"
Write-Host "Decrypt all databases:   .\windows.ps1 decrypt --account <wxid_directory>"
