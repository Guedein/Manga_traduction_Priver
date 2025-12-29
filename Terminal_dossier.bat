@echo off
cd /d "%~dp0"

REM Activer le venv
call .\.venv\Scripts\activate

REM Le terminal reste ouvert et utilisable
cmd /k

