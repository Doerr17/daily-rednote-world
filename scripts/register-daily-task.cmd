@echo off
setlocal EnableExtensions
set "TASK_NAME=DailyWorldRednote"
set "TASK_TIME=08:00"
for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"

if not defined PYTHON_EXE (
  echo ERROR: python.exe was not found on PATH.
  exit /b 1
)

if /I not "%~1"=="REGISTER" (
  echo Preview only: task "%TASK_NAME%" would run daily at %TASK_TIME%.
  echo Program: %PYTHON_EXE%
  echo Working directory: %PROJECT_ROOT%
  echo No task was created. Run this script with REGISTER to create it.
  exit /b 0
)

schtasks.exe /Create /F /SC DAILY /ST %TASK_TIME% /TN "%TASK_NAME%" /TR "\"%PYTHON_EXE%\" \"%PROJECT_ROOT%\main.py\" daily"
if errorlevel 1 exit /b %ERRORLEVEL%
echo Registered "%TASK_NAME%". It only generates review packages and never publishes.
