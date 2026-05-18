' Double-click launcher for the manager UI on Windows. Hidden cmd, no
' console flicker. Calls scripts\start_locallyai.bat with the "manager"
' argument so only the API + manager-ui dev server are started and
' only the manager UI tab is opened.

Set objFSO = CreateObject("Scripting.FileSystemObject")
Set objShell = CreateObject("WScript.Shell")

strRoot = objFSO.GetParentFolderName(WScript.ScriptFullName)
strBat  = strRoot & "\scripts\start_locallyai.bat"

If Not objFSO.FileExists(strBat) Then
    MsgBox "LocallyAI Manager: cannot find " & strBat, vbCritical, "LocallyAI Manager"
    WScript.Quit 1
End If

objShell.Run """" & strBat & """ manager", 0, False
