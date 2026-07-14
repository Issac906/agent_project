@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Register-Codex.ps1"
if errorlevel 1 pause
