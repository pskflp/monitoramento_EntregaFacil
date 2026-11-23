"""Monitor NOC: roda só as regras de alerta do SAP, em loop (Victor).

Independe do agente e do Gemini: não precisa de GOOGLE_API_KEY.

Uso, a partir da raiz do projeto:
    python monitor.py --uma-vez --hoje 2026-11-16     # um ciclo, mostra a tabela
    python monitor.py --intervalo 120 --hoje 2026-11-16
Para parar: Ctrl + C.
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from alertas import (
    ARQUIVOS, FUSO, abrir_aba_plantao, aplicar_regras, carregar_silver,
    filtrar_pedidos_do_dia, gravar_alertas, limitar_alertas,
)

PASTA = Path(__file__).resolve().parent
SILVER_REAL = PASTA / "data" / "entrada" / "silver"
SILVER_AMOSTRA = PASTA / "data" / "amostras" / "silver"


def pasta_silver_padrao() -> Path:
    """Usa os CSVs reais da Rebeca se estiverem todos lá; senão, a amostra sintética."""
    if all((SILVER_REAL / nome).exists() for nome in ARQUIVOS.values()):
        return SILVER_REAL
    print("Aviso: usando a AMOSTRA SINTÉTICA (coloque os 6 CSVs reais em data/entrada/silver).")
    return SILVER_AMOSTRA


def conectar_planilha():
    """Abre a aba Alertas se PLANTAO_SHEET_ID e a service account estiverem configurados."""
    sheet_id = os.getenv("PLANTAO_SHEET_ID", "").strip()
    credenciais = Path(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"))
    if not credenciais.is_absolute():
        credenciais = PASTA / credenciais
    if sheet_id and not credenciais.exists():
        print(f"PLANTAO_SHEET_ID configurado, mas não achei {credenciais.name} na pasta do projeto.")
    if sheet_id and credenciais.exists():
        folha = abrir_aba_plantao(sheet_id, str(credenciais))
        print(f"Planilha de plantão conectada (aba {folha.title}).")
        return folha
    print("Planilha de plantão não configurada: alertas só aparecem aqui.")
    return None


def ciclo(silver: str | None, folha, hoje: str | None, mostrar_tabela: bool = False,
          dia_pedido: str | None = None, max_por_regra: int = 0, limiar_surto: int = 0) -> int:
    """silver=None lê do Databricks; senão, lê os CSVs da pasta.

    dia_pedido: se informado, só os pedidos feitos nesse dia (limita o volume).
    max_por_regra / limiar_surto: ver limitar_alertas (evita saturar o plantão).
    """
    dia = pd.Timestamp(hoje) if hoje else pd.Timestamp(datetime.now(FUSO).date())
    if silver is None:
        from databricks_silver import carregar_silver_databricks

        dados = carregar_silver_databricks()
    else:
        dados = carregar_silver(silver)
    if dia_pedido:
        # "2026-10-18" = um dia; "2026-10-27:2026-10-29" = período
        inicio, _, fim = str(dia_pedido).partition(":")
        dados = filtrar_pedidos_do_dia(dados, pd.Timestamp(inicio), pd.Timestamp(fim) if fim else None)
    alertas = aplicar_regras(dados, dia)
    total = len(alertas)
    # Surto olha o total do ciclo; o limite por regra vale só para os NOVOS (fila anda)
    alertas = limitar_alertas(alertas, 0, limiar_surto)
    if len(alertas) != total:
        print(f"{total} alertas nas regras; surto aberto para as regras acima de {limiar_surto}.", flush=True)
    if mostrar_tabela:
        tabela = limitar_alertas(alertas, max_por_regra, 0)
        print(tabela.to_string(index=False) if len(tabela) else "Nenhum alerta.")
    novos = gravar_alertas(alertas, folha, max_por_regra) if folha is not None else 0
    print(f"[{datetime.now():%H:%M:%S}] {len(alertas)} alertas encontrados, {novos} novos na planilha.", flush=True)
    return len(alertas), novos


def main():
    parser = argparse.ArgumentParser(description="Monitor de alertas do SAP · PagFácil")
    parser.add_argument("--uma-vez", action="store_true", help="roda um ciclo e mostra a tabela")
    parser.add_argument("--intervalo", type=int, default=120, help="segundos entre ciclos")
    parser.add_argument("--silver", default=None,
                        help="pasta com os CSVs da Silver (padrão: data/entrada/silver ou a amostra)")
    parser.add_argument("--hoje", default=None, help="data de referência, ex.: 2026-11-16")
    parser.add_argument("--dia-pedido", default=None,
                        help="só pedidos deste dia (2026-10-29) ou período (2026-10-27:2026-10-29)")
    parser.add_argument("--max-por-regra", type=int, default=0,
                        help="no máximo N alertas individuais por regra (0 = sem limite)")
    parser.add_argument("--limiar-surto", type=int, default=0,
                        help="regra com mais de N pedidos no ciclo gera 1 alerta P1 de surto (0 = desligado)")
    args = parser.parse_args()
    limites = {"max_por_regra": args.max_por_regra, "limiar_surto": args.limiar_surto}

    from dotenv import load_dotenv

    load_dotenv(PASTA / ".env")
    import databricks_silver

    if args.silver:
        silver = Path(args.silver)
        print(f"Silver: {silver}")
    elif databricks_silver.configurado():
        # Sem fallback para a amostra: se o Databricks falhar, o ciclo falha (e
        # avisa), em vez de gravar alertas falsos na planilha de plantão.
        silver = None
        print("Silver: Databricks")
    else:
        silver = pasta_silver_padrao()
        print(f"Silver: {silver}")
    folha = conectar_planilha()

    if args.uma_vez:
        ciclo(silver, folha, args.hoje, mostrar_tabela=True, dia_pedido=args.dia_pedido, **limites)
        return
    # Sinal de vida: cada ciclo grava uma linha na aba Saude (disponibilidade do monitor)
    saude = None
    if folha is not None:
        from saude import Saude

        saude = Saude(folha, args.intervalo)
    origem = "Databricks" if silver is None else "CSV"
    try:
        while True:
            inicio = time.monotonic()
            try:
                total, novos = ciclo(silver, folha, args.hoje, dia_pedido=args.dia_pedido, **limites)
                if saude:
                    saude.registrar("OK", origem, total, novos, time.monotonic() - inicio)
            except Exception as falha:  # noqa: BLE001 - o monitor não pode parar sozinho
                print(f"[{datetime.now():%H:%M:%S}] falha no ciclo: {falha}. Tento de novo.", flush=True)
                if saude:
                    saude.registrar("FALHA", origem, duracao_s=time.monotonic() - inicio,
                                    erro=f"{type(falha).__name__}: {falha}")
            # mantém o ritmo: um ciclo a cada `intervalo` segundos, contando o tempo do próprio ciclo
            time.sleep(max(5, args.intervalo - (time.monotonic() - inicio)))
    except KeyboardInterrupt:
        print("Monitor encerrado.")


if __name__ == "__main__":
    main()
