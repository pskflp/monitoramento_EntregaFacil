"""Gera o histórico do plantão: os alertas reais do monitor, já resolvidos pela equipe.

1. Lê a Silver no Databricks e roda as MESMAS regras do monitor na base inteira
   (mesmo texto de detalhe), então o monitor reconhece cada linha depois e não duplica.
2. Separa os pedidos dos dias reservados para a demo ao vivo (--ao-vivo): eles ficam
   de fora, para o monitor encontrá-los na apresentação.
3. Para o resto, simula a operação: data de detecção pela data do pedido, aviso,
   reconhecimento e resolução com tempos variados (a maioria dentro do SLA, alguns
   fora e escalados), responsável da aba Equipe e a causa do catálogo do runbook.
4. Limpa a aba Alertas e grava tudo, em ordem de detecção (A-0001, A-0002...).

Uso, na pasta noc-monitoramento:
    python gerar_historico.py --simular          # mostra o resumo, não grava
    python gerar_historico.py                    # grava (apaga as linhas atuais da aba Alertas)
Opções: --ao-vivo 2026-10-27:2026-10-29  --hoje 2026-11-18  --limiar-surto 20  --semente 7
"""

from __future__ import annotations

import argparse
import math
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from alertas import (COLUNAS_PLANTAO, REGRAS, _cupom_do_alerta, abrir_aba_plantao,
                     aplicar_regras, garantir_cabecalho)

PASTA = Path(__file__).resolve().parent

# Dias entre o pedido e a detecção, por regra (quando a falha fica visível)
DETECCAO_DIAS = {
    "entrega_atrasada": (6, 12), "entrega_vencida": (8, 15), "pedido_parado": (3, 4),
    "entrega_parcial": (4, 8), "produto_divergente": (4, 8), "item_rejeitado": (1, 2),
    "inconsistencia_dados": (1, 2),
}
# Tempos (mediana e dispersão de uma log-normal) calibrados para ~95% no prazo
RESPOSTA_MIN = {"P1": (6, 0.5), "P2": (12, 0.55), "P3": (40, 0.6), "P4": (240, 0.7)}
RESOLUCAO_H = {"P1": (1.8, 0.5), "P2": (7, 0.9), "P3": (16, 0.7), "P4": (48, 0.6)}
ESCALA_MIN = {"P1": 15, "P2": 30, "P3": 120}
PRAZO_RESP_MIN = {"P1": 15, "P2": 30, "P3": 120, "P4": 1440}
PRAZO_RES_H = {"P1": 4, "P2": 24, "P3": 48, "P4": 120}
FORA_RESP, FORA_RES = 0.025, 0.035

# Causas reais de uma empresa de entregas (mesmo catálogo do runbook)
CAUSAS = {
    "entrega_atrasada": [
        (40, "Atraso da transportadora na rota; ocorrência registrada e Logística avisada. Cliente elegível a cupom {faixa}"),
        (25, "Separação atrasada no centro de distribuição por pico de pedidos; turno extra acionado. Cliente elegível a cupom {faixa}"),
        (20, "Prazo de entrega cadastrado curto para a região; Logística revisou a tabela de prazos. Cliente elegível a cupom {faixa}"),
        (15, "Destinatário ausente na 1ª tentativa; entregue na reentrega. Cliente elegível a cupom {faixa}"),
    ],
    "entrega_vencida": [
        (40, "Carga parada no hub da transportadora; coleta reagendada, nova previsão {d}"),
        (25, "Endereço incompleto; cliente contatado, endereço corrigido e nova previsão {d}"),
        (20, "Extravio confirmado pela transportadora; sinistro aberto e reenvio expresso com previsão {d}"),
        (15, "Devolvido ao remetente após 3 tentativas; reenvio agendado para {d}"),
    ],
    "pedido_parado": [
        (40, "Falta de estoque; reposição recebida e remessa criada para {d}"),
        (30, "Falha na integração do pedido com o SAP; pedido reprocessado e remessa criada"),
        (20, "Pagamento em análise antifraude; liberado pelo Financeiro e enviado à separação"),
        (10, "Pedido sem endereço válido; cliente contatado e pedido liberado"),
    ],
    "entrega_parcial": [
        (50, "Item faltante segue em remessa complementar com previsão {d}"),
        (30, "Erro de separação no CD; complemento enviado por entrega expressa, previsão {d}"),
        (20, "Item sem estoque; estorno parcial encaminhado ao Financeiro"),
    ],
    "produto_divergente": [
        (70, "Erro de separação; coleta reversa agendada e produto correto reenviado, previsão {d}"),
        (30, "Cadastro de material trocado; Dados corrigiu o cadastro e o produto correto foi reenviado"),
    ],
    "item_rejeitado": [
        (40, "Cancelamento pedido pelo cliente; estorno do item encaminhado ao Financeiro"),
        (35, "Ruptura de estoque do item; estorno encaminhado e restante do pedido mantido"),
        (25, "Bloqueio comercial por preço divergente; revisão comercial liberou o item"),
    ],
    "inconsistencia_dados": [
        (60, "Chave trocada na carga da Silver; chamado aberto para Dados e corrigido na carga seguinte"),
        (40, "Lançamento manual duplicado no SAP; registro duplicado removido"),
    ],
}
CAUSAS_SURTO = [
    (50, "Incidente: transportadora com coleta suspensa na região; Logística acionou a transportadora reserva e redistribuiu os pedidos"),
    (30, "Incidente: pico de pedidos no centro de distribuição; turno extra e prioridade aos pedidos mais antigos"),
    (20, "Incidente: falha na integração SAP x transportadora; Dados reprocessou as remessas e o fluxo normalizou"),
]


def lognormal(rnd: random.Random, mediana: float, sigma: float) -> float:
    return math.exp(rnd.gauss(math.log(mediana), sigma))


def dia_util(dt: datetime, rnd: random.Random) -> datetime:
    """Detecção em dia útil, entre 8h e 17h (o plantão P2-P4 é comercial)."""
    while dt.weekday() >= 5:
        dt += timedelta(days=1)
    return dt.replace(hour=rnd.randint(8, 16), minute=rnd.randint(0, 59), second=rnd.randint(0, 59))


def horario_comercial(t: datetime, minimo: datetime, maximo: datetime, rnd: random.Random) -> datetime:
    """Leva t para dentro do expediente (seg a sex, 8h-18h) sem sair de [minimo, maximo]."""
    def util(x: datetime) -> bool:
        return x.weekday() < 5 and 8 <= x.hour < 18
    if util(t):
        return t
    # 1) fim do expediente anterior (17h-17h59 do mesmo dia útil)
    antes = t.replace(hour=17, minute=rnd.randint(0, 59), second=rnd.randint(0, 59))
    if t.hour < 8:
        antes -= timedelta(days=1)
    while antes.weekday() >= 5:
        antes -= timedelta(days=1)
    # 2) começo do próximo expediente (8h-9h59)
    depois = (t if t.hour < 8 else t + timedelta(days=1)).replace(hour=8, minute=rnd.randint(0, 59), second=rnd.randint(0, 59))
    while depois.weekday() >= 5:
        depois += timedelta(days=1)
    for c in ((depois, antes) if rnd.random() < 0.5 else (antes, depois)):
        if minimo <= c <= maximo:
            return c
    return t


def escolher(rnd: random.Random, opcoes):
    pesos, textos = zip(*opcoes)
    return rnd.choices(textos, weights=pesos, k=1)[0]


def equipe(folha) -> tuple[list[str], list[str]]:
    try:
        v = folha.spreadsheet.worksheet("Equipe").get_all_values()
    except Exception:  # noqa: BLE001
        return ["N1"], ["N2"]
    cab = [c.strip().lower() for c in v[0]]
    i_n, i_niv = cab.index("nome"), cab.index("nivel")
    i_at = cab.index("ativo") if "ativo" in cab else -1
    ativos = [l for l in v[1:] if l[i_n] and (i_at < 0 or l[i_at].strip().upper() not in ("NÃO", "NAO", "N"))]
    n1 = [l[i_n] for l in ativos if l[i_niv].strip().upper() == "N1"] or ["N1"]
    n2 = [l[i_n] for l in ativos if l[i_niv].strip().upper() == "N2"] or n1
    return n1, n2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ao-vivo", default="2026-10-27:2026-10-29", help="pedidos deixados para a demo")
    ap.add_argument("--hoje", default="2026-11-18", help="mesma data de referência da demo")
    ap.add_argument("--limiar-surto", type=int, default=20)
    ap.add_argument("--semente", type=int, default=7)
    ap.add_argument("--simular", action="store_true")
    a = ap.parse_args()
    load_dotenv(PASTA / ".env")
    rnd = random.Random(a.semente)

    from databricks_silver import carregar_silver_databricks
    dados = carregar_silver_databricks()
    alertas = aplicar_regras(dados, pd.Timestamp(a.hoje))
    datas = dict(zip(dados["pedidos"]["id_pedido"].astype(str), dados["pedidos"]["data_pedido"]))
    ini, _, fim = a.ao_vivo.partition(":")
    vivo_ini, vivo_fim = pd.Timestamp(ini), pd.Timestamp(fim or ini)
    primeira = min(datas.values())

    linhas, reservados = [], 0
    for _, al in alertas.iterrows():
        dp = datas.get(str(al["id_pedido"]))
        if dp is not None and vivo_ini <= dp.normalize() <= vivo_fim:
            reservados += 1
            continue
        if dp is None:  # alerta sem pedido (ex.: remessa sem itens): data qualquer do período
            dp = primeira + timedelta(days=rnd.randint(0, (vivo_ini - primeira).days - 1))
        lo, hi = DETECCAO_DIAS.get(al["regra"], (1, 3))
        det = dia_util((dp + timedelta(days=rnd.randint(lo, hi))).to_pydatetime(), rnd)
        linhas.append({"regra": al["regra"], "id_pedido": al["id_pedido"], "detalhe": al["detalhe"],
                       "gravidade": al["gravidade"], "runbook": al["runbook"], "_det": det,
                       **_cupom_do_alerta(al["regra"], al["detalhe"])})

    # Surtos do histórico: mais de N alertas da mesma regra no mesmo dia
    por_dia: dict[tuple[str, str], list[dict]] = {}
    for l in linhas:
        por_dia.setdefault((l["regra"], l["_det"].date().isoformat()), []).append(l)
    for (regra, _), grupo in por_dia.items():
        if len(grupo) > a.limiar_surto:
            primeiro = min(g["_det"] for g in grupo)
            exemplos = ", ".join(str(g["id_pedido"]) for g in grupo[:5])
            linhas.append({"regra": f"surto_{regra}", "id_pedido": "—",
                           "detalhe": f"Surto: {len(grupo)} pedidos com {regra} no mesmo ciclo (ex.: {exemplos})",
                           "gravidade": "P2" if REGRAS.get(regra, ("P2",))[0] == "P4" else "P1",
                           "runbook": REGRAS.get(regra, ("", "RB-07"))[1], "_det": primeiro,
                           "cupom_elegivel": "NAO_ELEGIVEL"})

    folha = None if a.simular else abrir_aba_plantao(os.environ["PLANTAO_SHEET_ID"], _cred())
    n1, n2 = equipe(folha) if folha else (["Victor", "Rebeca"], ["Marcella", "Nathalia", "Maria"])
    fmt = "%Y-%m-%d %H:%M:%S"
    linhas.sort(key=lambda l: l["_det"])
    # Fora do prazo sob controle: ~2,5% na resposta e ~3,5% na resolução, por prioridade,
    # com pelo menos 1 caso fora (nada de 100%) e nunca abaixo da meta de 95%.
    fora_resp, fora_res = set(), set()
    for prio in ("P1", "P2", "P3", "P4"):
        idx = [i for i, l in enumerate(linhas) if l["gravidade"] == prio]
        if not idx:
            continue
        teto = max(1, int(len(idx) * 0.045))
        fora_resp |= set(rnd.sample(idx, min(teto, max(1, round(len(idx) * FORA_RESP)))))
        fora_res |= set(rnd.sample(idx, min(teto, max(1, round(len(idx) * FORA_RES)))))
    no_prazo_resp = no_prazo_res = escalados = 0
    por_prio: dict[str, list[int]] = {}
    for i, l in enumerate(linhas, start=1):
        g, det = l["gravidade"], l["_det"]
        lim_resp, lim_res = PRAZO_RESP_MIN[g], PRAZO_RES_H[g]
        if (i - 1) in fora_resp:
            resp_min = lim_resp * rnd.uniform(1.1, 1.8)
        else:
            resp_min = min(max(1.0, lognormal(rnd, *RESPOSTA_MIN[g])), lim_resp * rnd.uniform(0.6, 0.95))
        rec = det + timedelta(minutes=resp_min)
        if (i - 1) in fora_res:
            alvo, lo, hi = lim_res * rnd.uniform(1.05, 1.5), lim_res * 1.02, lim_res * 2.5
        else:
            lo = resp_min / 60 + 0.2
            alvo = min(max(lo, lognormal(rnd, *RESOLUCAO_H[g])), lim_res * rnd.uniform(0.7, 0.95))
            hi = lim_res * 0.98
        res = det + timedelta(hours=alvo)
        if g != "P1":  # P2 a P4 são trabalhados em horário comercial (seg a sex, 8h-18h)
            res = horario_comercial(res, det + timedelta(hours=lo), det + timedelta(hours=hi), rnd)
        res_h = (res - det).total_seconds() / 3600
        escalado = g in ESCALA_MIN and resp_min > ESCALA_MIN[g]
        quem = rnd.choice(n2 if escalado else n1)
        opcoes = CAUSAS_SURTO if l["regra"].startswith("surto_") else CAUSAS.get(l["regra"], [(1, "Caso verificado e tratado pela equipe responsável")])
        causa = escolher(rnd, opcoes).format(d=(res + timedelta(days=rnd.randint(2, 5))).strftime("%d/%m"),
                                            faixa=l.get("cupom_faixa") or "pela política")
        l.update({"alerta_id": f"A-{i:04d}", "detectado_em": det.strftime(fmt), "status": "RESOLVIDO",
                  "avisado_em": (det + timedelta(seconds=rnd.randint(20, 70))).strftime(fmt),
                  "reconhecido_em": rec.strftime(fmt), "resolvido_em": res.strftime(fmt),
                  "escalado_em": (det + timedelta(minutes=ESCALA_MIN[g])).strftime(fmt) if escalado else "",
                  "responsavel": quem, "causa": causa, "origem": "SAP", "reclamacoes_vinculadas": ""})
        escalados += escalado
        no_prazo_resp += resp_min <= lim_resp
        no_prazo_res += res_h <= lim_res
        conta = por_prio.setdefault(g, [0, 0, 0])
        conta[0] += 1
        conta[1] += resp_min <= lim_resp
        conta[2] += res_h <= lim_res

    n = len(linhas)
    print(f"Alertas na base: {len(alertas)} | reservados para a demo ({a.ao_vivo}): {reservados} | histórico: {n}")
    print(f"Surtos no histórico: {sum(l['regra'].startswith('surto_') for l in linhas)}")
    print(f"Detecção de {linhas[0]['detectado_em']} a {linhas[-1]['detectado_em']}")
    print(f"Resposta no prazo: {no_prazo_resp / n:.1%} | Resolução no prazo: {no_prazo_res / n:.1%} | Escalados: {escalados}")
    for prio, (q, r1, r2) in sorted(por_prio.items()):
        print(f"  {prio}: {q} casos | resposta no prazo {r1 / q:.1%} | resolução no prazo {r2 / q:.1%}")
    print(f"Equipe: N1 {n1} | N2 {n2}")
    print(pd.Series([l["regra"] for l in linhas]).value_counts().to_string())
    if a.simular:
        print("(nada gravado)")
        return

    garantir_cabecalho(folha)
    cab = folha.row_values(1)
    folha.batch_clear([f"A2:{_coluna(len(cab))}{max(folha.row_count, 2)}"])
    valores = [[str(l.get(c, "")) for c in cab] for l in linhas]
    for k in range(0, len(valores), 1000):
        folha.append_rows(valores[k:k + 1000], value_input_option="RAW", table_range="A1")
    print(f"Gravadas {n} linhas na aba Alertas. Próximo ID do monitor: A-{n + 1:04d}")
    gravar_saude(folha, cab, rnd, n1)


def gravar_saude(folha, cab: list[str], rnd: random.Random, n1: list[str]) -> None:
    """Sete dias de sinal de vida do monitor até agora, com duas paradas e uma falha.

    Parada de 14 min há 3 dias (abre e fecha um P1 monitor_parado, S-0001),
    parada de 6 min há ~20 h (abaixo de 10 min: não abre P1) e um ciclo com falha
    de leitura do Databricks há 5 dias. Disponibilidade ~99,8% em 7 dias e ~99,7% em 24 h.
    """
    from gspread.exceptions import WorksheetNotFound

    from alertas import FUSO
    from saude import ABA, COLUNAS, historico

    agora = datetime.now(FUSO).replace(tzinfo=None, microsecond=0)
    parada_grande = (agora - timedelta(days=3)).replace(hour=3, minute=12, second=0)
    parada_pequena = agora - timedelta(hours=20, minutes=17)
    falha = (agora - timedelta(days=5)).replace(hour=14, minute=40, second=0)
    linhas = historico(agora, 7, 2, rnd, [(parada_grande, 14), (parada_pequena, 6)], [falha])
    try:
        aba = folha.spreadsheet.worksheet(ABA)
    except WorksheetNotFound:
        aba = folha.spreadsheet.add_worksheet(title=ABA, rows=8000, cols=len(COLUNAS))
    aba.clear()
    aba.update(range_name="A1", values=[COLUNAS] + linhas, value_input_option="USER_ENTERED")

    # O P1 que a parada grande abriu (o Apps Script faz isso ao vivo): fechado sozinho na volta
    fmt = "%Y-%m-%d %H:%M:%S"
    det = parada_grande + timedelta(minutes=10, seconds=40)
    volta = parada_grande + timedelta(minutes=14, seconds=50)
    p1 = {"alerta_id": "S-0001", "regra": "monitor_parado", "id_pedido": "—",
          "detalhe": f"Monitor sem ciclo há 11 min (último às {(parada_grande - timedelta(minutes=1)).strftime('%d/%m %H:%M')})",
          "detectado_em": det.strftime(fmt), "gravidade": "P1", "runbook": "RB-10", "status": "RESOLVIDO",
          "avisado_em": (det + timedelta(seconds=30)).strftime(fmt), "reconhecido_em": (det + timedelta(minutes=3)).strftime(fmt),
          "resolvido_em": volta.strftime(fmt), "responsavel": rnd.choice(n1), "origem": "SAUDE",
          "causa": "Computador do monitor reiniciou por atualização do Windows; monitor reiniciado e ciclos normalizados",
          "cupom_elegivel": "NAO_ELEGIVEL"}
    folha.append_rows([[str(p1.get(c, "")) for c in cab]], value_input_option="RAW", table_range="A1")
    print(f"Aba {ABA}: {len(linhas)} ciclos em 7 dias (2 paradas, 1 falha) + P1 S-0001 de monitor parado.")


def _cred() -> str:
    c = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    return c if os.path.isabs(c) else str(PASTA / c)


def _coluna(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


if __name__ == "__main__":
    main()
