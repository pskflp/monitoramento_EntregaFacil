"""Lê a Silver/Gold direto do Databricks, no lugar dos 6 CSVs.

Devolve o mesmo dicionário de DataFrames de `alertas.carregar_silver`, então as
regras de alerta não mudam. As chaves (id_pedido, id_cliente, id_remessa...)
continuam as do dicionário da Rebeca, as mesmas usadas pelo agente de triagem.

Configuração no .env:
    DATABRICKS_SERVER_HOSTNAME=adb-xxxx.azuredatabricks.net
    DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/xxxx
    DATABRICKS_TOKEN=dapi...
    DATABRICKS_SCHEMA_SILVER=catalogo.schema      # prefixo das 6 tabelas
Cada tabela usa o nome do CSV sem ".csv" (ex.: tratamento_silver_pedidos).
Para mudar o nome de uma, defina DATABRICKS_TABELA_SILVER_<CHAVE>, por exemplo
DATABRICKS_TABELA_SILVER_PEDIDOS=catalogo.schema.gold_pedidos.
"""

from __future__ import annotations

import os
import re

import pandas as pd

from alertas import ARQUIVOS, padronizar_silver

_PARTE_SQL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def configurado() -> bool:
    """True se há credenciais e ao menos um destino de tabelas definido."""
    base = all(os.getenv(v, "").strip() for v in (
        "DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN"))
    return base and bool(_destinos())


def _destinos() -> dict[str, str]:
    """chave -> nome completo da tabela, a partir do schema e dos ajustes por tabela."""
    schema = os.getenv("DATABRICKS_SCHEMA_SILVER", "").strip().strip(".")
    # Prefixo dos nomes das tabelas. Padrão "tratamento_silver_" (nome dos CSVs);
    # defina DATABRICKS_PREFIXO_SILVER= (vazio) se as tabelas forem só "pedidos", etc.
    prefixo = os.getenv("DATABRICKS_PREFIXO_SILVER")
    destinos = {}
    for chave, arquivo in ARQUIVOS.items():
        especifico = os.getenv(f"DATABRICKS_TABELA_SILVER_{chave.upper()}", "").strip()
        if especifico:
            destinos[chave] = especifico
        elif schema:
            if prefixo is None:
                nome = arquivo.removesuffix(".csv")
            else:
                nome = f"{prefixo.strip()}{chave}"
            destinos[chave] = f"{schema}.{nome}"
    return destinos


def _nome_sql(tabela: str) -> str:
    """Valida o nome (evita injeção) e cita cada parte como identificador SQL."""
    partes = tabela.split(".")
    if not 1 <= len(partes) <= 3 or not all(_PARTE_SQL.fullmatch(p) for p in partes):
        raise ValueError(f"Nome de tabela inválido: {tabela!r}")
    return ".".join(f"`{p}`" for p in partes)


def carregar_silver_databricks() -> dict[str, pd.DataFrame]:
    """Lê as 6 tabelas e aplica a mesma padronização usada nos CSVs."""
    from databricks import sql  # pip install databricks-sql-connector

    destinos = _destinos()
    faltando = [c for c in ARQUIVOS if c not in destinos]
    if faltando:
        raise ValueError(f"Tabelas sem destino no Databricks: {faltando}. "
                         "Defina DATABRICKS_SCHEMA_SILVER ou DATABRICKS_TABELA_SILVER_<CHAVE>.")

    d: dict[str, pd.DataFrame] = {}
    with sql.connect(
        server_hostname=os.environ["DATABRICKS_SERVER_HOSTNAME"],
        http_path=os.environ["DATABRICKS_HTTP_PATH"],
        access_token=os.environ["DATABRICKS_TOKEN"],
    ) as conexao, conexao.cursor() as cursor:
        for chave, tabela in destinos.items():
            cursor.execute(f"SELECT * FROM {_nome_sql(tabela)}")
            colunas = [descricao[0] for descricao in (cursor.description or [])]
            linhas = [list(linha) for linha in cursor.fetchall()]
            # Tudo como texto, como nos CSVs; a padronização converte datas e números.
            tabela_df = pd.DataFrame(linhas, columns=colunas).astype("string")
            d[chave] = tabela_df.replace({"null": pd.NA, "NULL": pd.NA, "": pd.NA})
    return padronizar_silver(d)
