@echo off
rem ============================================================
rem  CodePoint launcher  (ASCII only / CRLF required)
rem
rem    run.bat                start (no console window)
rem    run.bat --console      start and keep console output
rem    run.bat --where        only print the python.exe it picked
rem    run.bat <other args>   console mode, args passed to main.py
rem                           e.g. run.bat --selftest
rem
rem  NOTE: keep this file CRLF + ASCII, or cmd.exe will misparse it.
rem ============================================================
setlocal enableextensions
set "HERE=%~dp0"
set "PYEXE="
set "CONSOLE="
set "WHEREONLY="

if /i "%~1"=="--where"   set "WHEREONLY=1"
if /i "%~1"=="--console" set "CONSOLE=1"
if not defined WHEREONLY if not defined CONSOLE if not "%~1"=="" set "CONSOLE=1"

rem ---- 1) well known install locations, first one with tkinter wins ----
rem      order = python version descending; C:\Espressif sits between 3.11 and
rem      3.10 on purpose (it is a stable 3.11.15 and beats a 3.10 beta).
call :probe "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :probe "C:\Python313\python.exe"
call :probe "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :probe "C:\Python312\python.exe"
call :probe "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :probe "C:\Python311\python.exe"
call :probe "C:\Espressif\tools\python\python.exe"
call :probe "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
call :probe "C:\Python310\python.exe"

rem ---- 2) fall back to whatever "python.exe" PATH resolves to ----
if not defined PYEXE for /f "delims=" %%P in ('where python.exe 2^>nul') do call :probe "%%P"

if not defined PYEXE goto :no_python
if defined WHEREONLY goto :print_where
if defined CONSOLE goto :launch_console
goto :launch_gui

rem ============================================================
:probe
rem  if %1 exists and can import tkinter, remember it
if defined PYEXE exit /b 0
if not exist %1 exit /b 0
%1 -c "import tkinter" >nul 2>nul
if errorlevel 1 exit /b 0
set "PYEXE=%~1"
exit /b 0

:print_where
echo %PYEXE%
exit /b 0

:no_python
echo.
echo [ERROR] No Python with tkinter was found.
echo         1) install Python 3.9+ from https://www.python.org/downloads/
echo            (tkinter is included by default, tick "Add to PATH")
echo         2) or open run.bat and add your python.exe path to the :probe list
echo.
echo Tried:
echo   %LOCALAPPDATA%\Programs\Python\Python31x\python.exe
echo   C:\Python31x\python.exe
echo   C:\Espressif\tools\python\python.exe
echo   python.exe from PATH
echo.
pause
exit /b 1

:launch_gui
rem  prefer pythonw.exe so no black console window shows up
set "PYW=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PYEXE%"
echo Using: %PYEXE%
pushd "%HERE%"
start "" "%PYW%" "%HERE%main.py"
popd
exit /b 0

:launch_console
echo Using: %PYEXE%
pushd "%HERE%"
if /i "%~1"=="--console" (
  "%PYEXE%" "%HERE%main.py"
) else (
  "%PYEXE%" "%HERE%main.py" %*
)
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
  echo.
  echo [exit code %RC%]
  echo Log file: %%APPDATA%%\CodePoint\logs\codepoint.log
  pause
)
exit /b %RC%
