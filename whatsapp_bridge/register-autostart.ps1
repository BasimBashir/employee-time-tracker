# Registers the WhatsApp bridge to start (hidden) when the current user logs
# on - the same HKCU Run-key pattern the tray app uses. Also starts the bridge
# right away, so pairing doesn't wait for the next logon.
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$vbs = Join-Path $dir "start-hidden.vbs"

# The installed bridge ships its own node.exe; the dev checkout falls back to PATH.
if (-not (Test-Path (Join-Path $dir "node.exe")) -and $null -eq (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Error "node.exe was not found beside the bridge and not on PATH - install Node.js 20+ first."
    exit 1
}

New-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "EmployeeTrackerWhatsAppBridge" -Value "wscript.exe `"$vbs`"" -PropertyType String -Force | Out-Null

Write-Host "Registered 'EmployeeTrackerWhatsAppBridge' for logon auto-start."
Start-Process wscript.exe -ArgumentList "`"$vbs`"" -WindowStyle Hidden
Write-Host "Bridge started. Open http://127.0.0.1:8090/status to scan the QR."
