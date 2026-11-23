@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
title Monitor NOC (operacao)
echo Monitor em operacao: base inteira, ate 10 alertas novos por regra a cada ciclo, surto P1 acima de 20.
echo Roda a cada 120 s. O registro fica em monitor_log.txt. Para parar, feche esta janela.
".venv\Scripts\python.exe" -u monitor.py --intervalo 120 --max-por-regra 10 --limiar-surto 20 > monitor_log.txt 2>&1
