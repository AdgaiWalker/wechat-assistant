param(
    [Parameter(Position = 0)]
    [ValidateSet("auto", "status", "verify", "extract", "decrypt", "serve", "export")]
    [string]$Command = "auto",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = "python"
$ToolArgs = @($RemainingArgs)
$ManageAccountEnvironment = $Command -in @("serve", "export")
$PreviousAccountEnvironment = $env:WECHAT_DECRYPT_ACCOUNT

if ($ManageAccountEnvironment) {
    Remove-Item Env:WECHAT_DECRYPT_ACCOUNT -ErrorAction SilentlyContinue
    $FilteredArgs = @()
    for ($Index = 0; $Index -lt $ToolArgs.Count; $Index++) {
        if ($ToolArgs[$Index] -eq "--account") {
            if ($Index + 1 -ge $ToolArgs.Count) {
                Write-Error "--account requires an account directory name"
                exit 2
            }
            $env:WECHAT_DECRYPT_ACCOUNT = $ToolArgs[$Index + 1]
            $Index++
        } else {
            $FilteredArgs += $ToolArgs[$Index]
        }
    }
    $ToolArgs = $FilteredArgs
}

switch ($Command) {
    "auto" {
        & $Python (Join-Path $ProjectRoot "scripts\windows\extract_raw_key.py") @RemainingArgs
        $Status = $LASTEXITCODE
        if ($Status -eq 10) {
            Write-Host "No reusable key. Starting one-time key capture..."
            & $Python (Join-Path $ProjectRoot "scripts\windows\extract_raw_key.py") --force @RemainingArgs
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        } elseif ($Status -ne 0) {
            exit $Status
        }
        & $Python (Join-Path $ProjectRoot "scripts\windows\decrypt_all.py") @RemainingArgs
    }
    "status" {
        & $Python (Join-Path $ProjectRoot "scripts\windows\extract_raw_key.py") @RemainingArgs
    }
    "verify" {
        & $Python (Join-Path $ProjectRoot "scripts\windows\decrypt_all.py") --verify-only @RemainingArgs
    }
    "extract" {
        & $Python (Join-Path $ProjectRoot "scripts\windows\extract_raw_key.py") --force @RemainingArgs
    }
    "decrypt" {
        & $Python (Join-Path $ProjectRoot "scripts\windows\decrypt_all.py") @RemainingArgs
    }
    "serve" {
        & $Python (Join-Path $ProjectRoot "server.py") @ToolArgs
    }
    "export" {
        & $Python (Join-Path $ProjectRoot "scripts\common\export_chat.py") @ToolArgs
    }
}

$ExitCode = $LASTEXITCODE
if ($ManageAccountEnvironment) {
    if ([string]::IsNullOrEmpty($PreviousAccountEnvironment)) {
        Remove-Item Env:WECHAT_DECRYPT_ACCOUNT -ErrorAction SilentlyContinue
    } else {
        $env:WECHAT_DECRYPT_ACCOUNT = $PreviousAccountEnvironment
    }
}
exit $ExitCode
