@echo off
rem Daily runner for the Claude Code usage report. Logs to run.log.
set DIR=C:\Users\charl\source\repos\cc-usage-analytics
echo [%DATE% %TIME%] start >> "%DIR%\run.log"
"C:\Users\charl\anaconda3\python.exe" "%DIR%\report.py" >> "%DIR%\run.log" 2>&1
echo [%DATE% %TIME%] exit %ERRORLEVEL% >> "%DIR%\run.log"
