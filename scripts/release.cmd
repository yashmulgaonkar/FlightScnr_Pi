@echo off
REM SPDX-License-Identifier: CC-BY-NC-SA-4.0
REM Copyright (c) 2026 Yash Mulgaonkar - https://github.com/yashmulgaonkar/FlightScnr_Pi
REM
REM Windows cmd/PowerShell wrapper for dev-release.sh (no ExecutionPolicy needed).
REM   .\scripts\release.cmd --dry-run
REM macOS/Linux: ./scripts/dev-release.sh --dry-run

setlocal
set "SCRIPT_DIR=%~dp0"
set "BASH="

if exist "%ProgramFiles%\Git\usr\bin\bash.exe" set "BASH=%ProgramFiles%\Git\usr\bin\bash.exe"
if not defined BASH if exist "%ProgramFiles(x86)%\Git\usr\bin\bash.exe" set "BASH=%ProgramFiles(x86)%\Git\usr\bin\bash.exe"
if not defined BASH (
  where bash.exe >nul 2>&1 && for /f "delims=" %%i in ('where bash.exe') do (
    set "BASH=%%i"
    goto :have_bash
  )
)

:have_bash
if not defined BASH (
  echo Git Bash not found. Install Git for Windows, or run from Git Bash:
  echo   ./scripts/dev-release.sh %*
  exit /b 1
)

REM Prefer real CPython over the Microsoft Store python3 stub.
if exist "%ProgramFiles%\Python311\python.exe" set "PATH=%ProgramFiles%\Python311;%PATH%"

"%BASH%" "%SCRIPT_DIR%dev-release.sh" %*
exit /b %ERRORLEVEL%
