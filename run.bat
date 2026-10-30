@echo off
rem Inicia o AJAZZ Gamepad e abre a interface no navegador.
cd /d "%~dp0"
python app.py %*
if errorlevel 1 pause
