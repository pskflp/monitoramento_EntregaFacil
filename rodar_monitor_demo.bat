@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
title Monitor NOC (DEMO)
echo Monitor da DEMO: pedidos de 27 a 29/10 (o resto ja esta no historico), 3 alertas novos por regra a cada ciclo de 1 min, surto P1 acima de 5.
echo Roda a cada 60 s. O registro fica em monitor_demo_log.txt. Para parar, feche esta janela.
".venv\Scripts\python.exe" -u monitor.py --intervalo 60 --dia-pedido 2026-10-27:2026-10-29 --hoje 2026-11-18 --max-por-regra 3 --limiar-surto 5 > monitor_demo_log.txt 2>&1
