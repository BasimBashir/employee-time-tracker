# Registers the tray app to start when the current user logs on (HKCU Run key).
$path = "$env:ProgramFiles\EmployeeTracker\tray\tray_app.exe"
New-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" `
  -Name "EmployeeTrackerTray" -Value $path -PropertyType String -Force
