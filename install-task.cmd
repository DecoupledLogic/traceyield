@echo off
rem install-task.cmd — register (or refresh) this machine's daily scheduled task.
rem
rem Portable across machines, like run.cmd: it points a Windows Task Scheduler
rem daily task at run.cmd in this same folder (%~dp0), so a fresh clone just runs
rem this once. Re-running it overwrites the existing task (/f), so it's safe to
rem run again to change the time.
rem
rem Usage:
rem   install-task.cmd            register the task at the default 06:00
rem   install-task.cmd 09:00      register it at a custom HH:MM time
rem
rem If `python` isn't on PATH for the account the task runs as, set the PYTHON
rem environment variable persistently (a user/system env var the task inherits,
rem not a `set` in one shell) — run.cmd honors it. See docs/guides/08.
setlocal
set DIR=%~dp0
set TASKNAME=TraceYield Usage Report
set RUNTIME=%~1
if not defined RUNTIME set RUNTIME=06:00

schtasks /create /tn "%TASKNAME%" /tr "\"%DIR%run.cmd\"" /sc daily /st %RUNTIME% /f
if errorlevel 1 (
  echo FAILED to create scheduled task "%TASKNAME%".
  endlocal
  exit /b 1
)

echo.
echo Installed daily task "%TASKNAME%" at %RUNTIME%, running:
echo   %DIR%run.cmd
echo.
schtasks /query /tn "%TASKNAME%"
endlocal
