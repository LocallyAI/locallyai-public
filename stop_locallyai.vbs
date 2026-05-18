' Double-click stopper for Windows. Hidden, no console flicker.

Set objFSO = CreateObject("Scripting.FileSystemObject")
Set objShell = CreateObject("WScript.Shell")

strRoot = objFSO.GetParentFolderName(WScript.ScriptFullName)
strBat  = strRoot & "\scripts\stop_locallyai.bat"

If Not objFSO.FileExists(strBat) Then
    MsgBox "LocallyAI: cannot find " & strBat, vbCritical, "Stop LocallyAI"
    WScript.Quit 1
End If

objShell.Run """" & strBat & """", 0, False
