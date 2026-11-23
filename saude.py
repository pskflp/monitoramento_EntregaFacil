"""Saúde do monitor: cada ciclo grava uma linha na aba "Saude" (sinal de vida).

Com isso a planilha mede a DISPONIBILIDADE do monitoramento:
    disponibilidade = 1 - (minutos parado / minutos observados)
e o Apps Script abre um P1 "monitor_parado" se passar muito tempo sem ciclo.

Colunas: ciclo_em | status (OK/FALHA) | origem | alertas | novos | duracao_s |
         gap_min (minutos desde o ciclo anterior) | parado_min | erro

parado_min: um ciclo OK conta como parado só o que passou da tolerância
(2 intervalos, para não punir variação normal); um ciclo FALHA conta o intervalo
inteiro (o monitor estava vivo, mas cego).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from alertas import FUSO

ABA = "Saude"
COLUNAS = ["ciclo_em", "status", "origem", "alertas", "novos", "duracao_s", "gap_min", "parado_min", "erro"]
FORMATO = "%Y-%m-%d %H:%M:%S"
MANTER_LINHAS = 7000  # ~9 dias a cada 2 min; o resto é apagado aos poucos


def parado(gap_min: float | None, status: str, intervalo_min: float) -> float:
    if gap_min is None:
        return 0.0
    if status != "OK":
        return round(gap_min, 1)
    return round(max(0.0, gap_min - 2 * intervalo_min), 1)


class Saude:
    def __init__(self, folha_alertas, intervalo_s: int):
        from gspread.exceptions import WorksheetNotFound

        planilha = folha_alertas.spreadsheet
        try:
            self.folha = planilha.worksheet(ABA)
        except WorksheetNotFound:
            self.folha = planilha.add_worksheet(title=ABA, rows=MANTER_LINHAS + 200, cols=len(COLUNAS))
            self.folha.update(range_name="A1", values=[COLUNAS])
        self.intervalo_min = intervalo_s / 60
        coluna = self.folha.col_values(1)
        self.linhas = len(coluna)
        self.ultimo = None
        if len(coluna) > 1:
            for formato in (FORMATO, "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
                try:
                    self.ultimo = datetime.strptime(coluna[-1], formato).replace(tzinfo=FUSO)
                    break
                except ValueError:
                    continue

    def registrar(self, status: str, origem: str, alertas: int = 0, novos: int = 0,
                  duracao_s: float = 0.0, erro: str = "") -> None:
        agora = datetime.now(FUSO)
        gap = (agora - self.ultimo).total_seconds() / 60 if self.ultimo else None
        linha = [agora.strftime(FORMATO), status, origem, alertas, novos, round(duracao_s, 1),
                 round(gap, 1) if gap is not None else "", parado(gap, status, self.intervalo_min), erro[:200]]
        try:
            self.folha.append_row(linha, value_input_option="USER_ENTERED", table_range="A1")
            self.ultimo = agora
            self.linhas += 1
            if self.linhas > MANTER_LINHAS + 500:  # apaga as mais antigas, de vez em quando
                self.folha.delete_rows(2, self.linhas - MANTER_LINHAS)
                self.linhas = MANTER_LINHAS
        except Exception as falha:  # noqa: BLE001 - registrar a saúde não pode derrubar o monitor
            print(f"Aviso: não gravei a saúde do ciclo ({type(falha).__name__}).", flush=True)


def historico(fim: datetime, dias: int, intervalo_min: float, rnd: random.Random,
              paradas: list[tuple[datetime, float]], falhas: list[datetime]) -> list[list]:
    """Linhas de saúde de `dias` dias até `fim`, com as paradas (início, minutos) e falhas dadas."""
    t, linhas, anterior = fim - timedelta(days=dias), [], None
    while t <= fim:
        if any(ini <= t < ini + timedelta(minutes=m) for ini, m in paradas):
            t += timedelta(minutes=intervalo_min)
            continue
        status = "FALHA" if any(abs((t - f).total_seconds()) < 60 * intervalo_min / 2 for f in falhas) else "OK"
        quando = t + timedelta(seconds=rnd.randint(0, 25))
        gap = (quando - anterior).total_seconds() / 60 if anterior else None
        dur = rnd.uniform(18, 45) if status == "OK" else 30.0
        linhas.append([quando.strftime(FORMATO), status, "Databricks",
                       0, 0, round(dur, 1), round(gap, 1) if gap is not None else "",
                       parado(gap, status, intervalo_min),
                       "" if status == "OK" else "Timeout ao consultar o Databricks"])
        anterior = quando
        t += timedelta(minutes=intervalo_min)
    return linhas
