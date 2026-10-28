[Setup]
AppName=Employee Tracker
AppVersion=0.1.0
DefaultDirName={autopf}\EmployeeTracker
DefaultGroupName=Employee Tracker
OutputDir=dist
OutputBaseFilename=EmployeeTrackerSetup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "dist\service\*"; DestDir: "{app}\service"; Flags: recursesubdirs
Source: "dist\tray\*"; DestDir: "{app}\tray"; Flags: recursesubdirs
Source: "dist\ui\*"; DestDir: "{app}\ui"; Flags: recursesubdirs
Source: "dist\bridge\*"; DestDir: "{commonappdata}\EmployeeTracker\bridge"; Flags: recursesubdirs
Source: "install-service.bat"; DestDir: "{app}\installer"
Source: "register-autostart.ps1"; DestDir: "{app}\installer"
; Stock detector for a fresh machine; never overwrite a promoted model on upgrade.
Source: "..\yolo11n.onnx"; DestDir: "{commonappdata}\EmployeeTracker\models"; Flags: onlyifdoesntexist

[Dirs]
Name: "{commonappdata}\EmployeeTracker\clips"
Name: "{commonappdata}\EmployeeTracker\data"
Name: "{commonappdata}\EmployeeTracker\logs"
Name: "{commonappdata}\EmployeeTracker\models"
Name: "{commonappdata}\EmployeeTracker\config"
; The bridge writes its session + log here as the logged-in user, so it must
; not sit under Program Files; users-modify keeps it writable on locked-down
; machines too.
Name: "{commonappdata}\EmployeeTracker\bridge"; Permissions: users-modify

[Icons]
; Desktop + Start Menu: opens the dashboard window (Electron loads the
; service-served UI at 127.0.0.1:8080, falling back to the local bundle).
Name: "{commondesktop}\Employee Tracker"; Filename: "{app}\ui\node_modules\electron\dist\electron.exe"; Parameters: """{app}\ui"""; WorkingDir: "{app}\ui"
Name: "{group}\Employee Tracker Dashboard"; Filename: "{app}\ui\node_modules\electron\dist\electron.exe"; Parameters: """{app}\ui"""; WorkingDir: "{app}\ui"

[Run]
Filename: "{app}\installer\install-service.bat"; Parameters: """{app}"""; Flags: runhidden
Filename: "powershell"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\register-autostart.ps1"""; Flags: runhidden
Filename: "powershell"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{commonappdata}\EmployeeTracker\bridge\register-autostart.ps1"""; Flags: runhidden
Filename: "{app}\tray\tray_app.exe"; Description: "Show the Employee Tracker tray icon now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "sc"; Parameters: "stop EmployeeTrackerService"; Flags: runhidden
Filename: "sc"; Parameters: "delete EmployeeTrackerService"; Flags: runhidden
