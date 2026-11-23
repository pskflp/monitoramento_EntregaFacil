@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
title Gravando o historico na aba Alertas
".venv\Scripts\python.exe" -u gerar_historico.py > historico_log.txt 2>&1
