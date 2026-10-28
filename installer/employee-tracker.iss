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

[Run]
Filename: "{app}\installer\install-service.bat"; Parameters: """{app}"""; Flags: runhidden
Filename: "powershell"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\register-autostart.ps1"""; Flags: runhidden
Filename: "powershell"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{commonappdata}\EmployeeTracker\bridge\register-autostart.ps1"""; Flags: runhidden

[UninstallRun]
Filename: "sc"; Parameters: "stop EmployeeTrackerService"; Flags: runhidden
Filename: "sc"; Parameters: "delete EmployeeTrackerService"; Flags: runhidden
