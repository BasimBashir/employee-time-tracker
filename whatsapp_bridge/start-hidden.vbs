' Starts the WhatsApp bridge with no visible window (auto-start entry runs
' this through wscript.exe). Output lands in bridge.log next to this file.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
sh.Run "cmd /c node.exe index.js >> bridge.log 2>&1", 0, False
