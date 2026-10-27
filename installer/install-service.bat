@echo off
rem Registers and starts the Employee Tracker Windows Service. Admin required.
rem %1 = app root (the directory containing service\service_main.exe).
rem      Defaults to the current directory when run by hand.
setlocal
set "APPDIR=%~1"
if "%APPDIR%"=="" set "APPDIR=%CD%"

sc create EmployeeTrackerService binPath= "\"%APPDIR%\service\service_main.exe\"" start= auto DisplayName= "Employee Tracker Service"
sc description EmployeeTrackerService "Captures entry/exit crossings and records attendance."
sc failure EmployeeTrackerService reset= 30 actions= restart/5000/restart/10000/restart/30000
net start EmployeeTrackerService
endlocal
