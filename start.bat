@echo off
cd /d "%~dp0"
node start.mjs
if errorlevel 1 pause
