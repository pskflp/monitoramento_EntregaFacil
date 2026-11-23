@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
title Simulacao do N1: resolver
".venv\Scripts\python.exe" -u simular_resolucao.py --etapa resolver --resolver 100 > simular_resolver_log.txt 2>&1
