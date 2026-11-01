@echo off
title Gerar executavel do KioBot EX
cd /d "%~dp0"
py -3.11 -m pip install -r requirements.txt pyinstaller
py -3.11 -m PyInstaller --noconfirm --windowed --name KioBotEX --icon assets\icon.ico ^
  --add-data "assets;assets" --hidden-import relay --hidden-import protocol --hidden-import tunnel main.py
echo.
echo Pronto. O programa esta em dist\KioBotEX\KioBotEX.exe
pause
