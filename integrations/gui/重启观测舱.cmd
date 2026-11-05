@echo off
rem Restart the eco observatory service:
rem kill the running eco_gui collector (so new collector code + new dist load),
rem then relaunch the launcher (it starts a fresh service and opens the UI).
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*eco_gui*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
ping -n 2 127.0.0.1 >nul
for %%f in ("%~dp0*v3.2.0.pyw") do start "" pythonw.exe "%%f"
exit
