@echo off
rem Daily runner for the Claude Code usage report.
rem Portable across machines: the repo dir is this script's own folder (%~dp0),
rem and per-machine artifacts (report.html, *_metrics.json, run.log) land under
rem machines\<machine-id>\ — report.py --machine-dir resolves that folder.
rem Set PYTHON to a specific interpreter if `python` isn't on PATH
rem   (e.g. set PYTHON=C:\Users\charl\anaconda3\python.exe before scheduling).
setlocal
set DIR=%~dp0
if not defined PYTHON set PYTHON=python
for /f "delims=" %%i in ('%PYTHON% "%DIR%report.py" --machine-dir') do set MDIR=%%i
if not exist "%MDIR%" mkdir "%MDIR%"
echo [%DATE% %TIME%] start >> "%MDIR%\run.log"
%PYTHON% "%DIR%report.py" >> "%MDIR%\run.log" 2>&1
echo [%DATE% %TIME%] exit %ERRORLEVEL% >> "%MDIR%\run.log"
endlocal
