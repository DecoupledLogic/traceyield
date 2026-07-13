@echo off
rem uninstall-task.cmd — remove this machine's daily scheduled task.
rem
rem The inverse of install-task.cmd. It deletes the Task Scheduler entry only;
rem it never touches this machine's data under machines\<machine-id>\ (metrics,
rem report.html, run.log all stay put). Safe to run when no task exists — it
rem reports that and exits cleanly.
rem
rem Usage:
rem   uninstall-task.cmd
setlocal
set TASKNAME=TraceYield Usage Report

schtasks /query /tn "%TASKNAME%" >nul 2>&1
if errorlevel 1 (
  echo No scheduled task named "%TASKNAME%" is registered. Nothing to do.
  endlocal
  exit /b 0
)

schtasks /delete /tn "%TASKNAME%" /f
if errorlevel 1 (
  echo FAILED to delete scheduled task "%TASKNAME%".
  endlocal
  exit /b 1
)

echo Removed scheduled task "%TASKNAME%". Local data under machines\ is untouched.
endlocal
