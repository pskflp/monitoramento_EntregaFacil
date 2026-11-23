@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
title Preparar e diagnosticar o monitoramento
(
echo == Python
where python
python --version
if not exist ".venv\Scripts\python.exe" (
  echo == Criando .venv
  python -m venv .venv
)
echo == Instalando bibliotecas
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
".venv\Scripts\python.exe" -u diagnostico.py
) > diagnostico_log.txt 2>&1
