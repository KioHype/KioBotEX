@echo off
title KioBot EX
cd /d "%~dp0"

py -3.11 --version >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 nao encontrado.
  echo Instale em https://www.python.org/downloads/release/python-3119/
  echo e marque a opcao "Add Python to PATH" durante a instalacao.
  pause
  exit /b 1
)

if not exist ".deps_ok" (
  echo Instalando dependencias pela primeira vez. Isso pode levar alguns minutos...
  py -3.11 -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Falha ao instalar as dependencias. Verifique a internet e tente de novo.
    pause
    exit /b 1
  )
  echo ok> .deps_ok
)

start "" pyw -3.11 main.py
