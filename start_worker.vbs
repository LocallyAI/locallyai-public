' Double-click launcher for the worker UI on Windows. Hidden cmd, no
' console flicker. Calls scripts\start_locallyai.bat with the "worker"
' argument so only the API + worker-ui dev server are started and
' only the worker UI tab is opened.

Set objFSO = CreateObject("Scripting.FileSystemObject")
Set objShell = CreateObject("WScript.Shell")

strRoot = objFSO.GetParentFolderName(WScript.ScriptFullName)
strBat  = strRoot & "\scripts\start_locallyai.bat"

If Not objFSO.FileExists(strBat) Then
    MsgBox "LocallyAI Worker: cannot find " & strBat, vbCritical, "LocallyAI Worker"
    WScript.Quit 1
End If

' 0 = hidden, False = don't wait. Pass "worker" as the only argument.
objShell.Run """" & strBat & """ worker", 0, False
