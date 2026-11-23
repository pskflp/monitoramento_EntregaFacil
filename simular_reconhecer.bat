@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
title Simulacao do N1: reconhecer
".venv\Scripts\python.exe" -u simular_resolucao.py --etapa reconhecer > simular_reconhecer_log.txt 2>&1
