Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = "D:\GDWPMA-AI"
ws.Run "cmd /c py app.py > data\server.log 2>&1", 0, False
WScript.Sleep 3000
ws.Run "http://127.0.0.1:5210"
