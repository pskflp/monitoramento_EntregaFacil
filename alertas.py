"""Regras de alerta sobre a camada Silver do SAP (Victor, NOC).

Lê os 6 CSVs da Silver (dicionário da Rebeca), aplica as regras e devolve
um alerta por pedido e regra. A gravação na planilha de plantão não repete
alertas que já existem (mesma regra + mesmo pedido).

Uso rápido (sem planilha), a partir da raiz do projeto:
    python alertas.py data/amostras/silver 2026-11-16
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

FUSO = ZoneInfo("America/Sao_Paulo")

ARQUIVOS = {
    "clientes": "tratamento_silver_clientes.csv",
    "pedidos": "tratamento_silver_pedidos.csv",
    "itens_pedido": "tratamento_silver_itens_pedido.csv",
    "materiais": "tratamento_silver_materiais.csv",
    "remessas": "tratamento_silver_remessas.csv",
    "itens_remessa": "tratamento_silver_itens_remessa.csv",
}

# regra -> (gravidade, procedimento do runbook)
REGRAS = {
    "entrega_atrasada":     ("P2", "RB-01"),
    "entrega_vencida":      ("P2", "RB-02"),
    "pedido_parado":        ("P2", "RB-03"),
    "entrega_parcial":      ("P3", "RB-04"),
    "produto_divergente":   ("P2", "RB-05"),
    "item_rejeitado":       ("P3", "RB-06"),
    "inconsistencia_dados": ("P4", "RB-07"),
}

# Esquema único da aba Alertas, compartilhado com a ponte do agente Entrega Fácil
# (ponte_agente.py). As 14 primeiras colunas não mudam de posição
# (o Apps Script as localiza pela posição); as novas ficam no fim.
COLUNAS_PLANTAO = [
    "alerta_id", "regra", "id_pedido", "detalhe", "detectado_em", "gravidade",
    "runbook", "status", "reconhecido_em", "resolvido_em", "responsavel",
    "causa", "avisado_em", "escalado_em",
    "origem", "id_reclamacao", "recebida_em", "tipo_contestacao", "produto",
    "valor_informado", "triada_em", "tempo_triagem_min", "procede",
    "dias_atraso", "cupom", "reclamacoes_vinculadas", "cupom_elegivel",
    # integração com o agente Entrega Fácil (ponte_agente.py)
    "cupom_faixa", "equipe",
]

# ---------------------------------------------------------------- cupom
# Mesma tabela do agente Entrega Fácil (tools/politica_cupons.py, da Nathália):
# o plantão só indica a faixa; quem entrega o cupom ao cliente é o agente.
# ponte_agente.py confere esta cópia com o arquivo do agente ao iniciar.
FAIXAS_CUPOM = [
    # (dias_min, dias_max, código, benefício)
    (1, 1, "EF-FRETE", "frete grátis"),
    (2, 2, "EF-CORTESIA5", "5% de desconto"),
    (3, 4, "EF-GARANTIA10", "10% de desconto"),
    (5, 7, "EF-COMPROMISSO15", "15% de desconto"),
    (8, 14, "EF-PRIORIDADE20", "20% + prioridade"),
    (15, None, "EF-PREMIUM25", "25% (ou reembolso pela revisão humana)"),
]


def faixa_cupom(dias: int) -> str:
    """Código da faixa para N dias de atraso confirmado ('' se não houver atraso)."""
    for minimo, maximo, codigo, _ in FAIXAS_CUPOM:
        if dias >= minimo and (maximo is None or dias <= maximo):
            return codigo
    return ""


def dias_no_detalhe(detalhe: str) -> int:
    achado = re.search(r"(\d+)\s*dia", str(detalhe or ""))
    return int(achado.group(1)) if achado else 0


# ---------------------------------------------------------------- leitura
def _id(serie: pd.Series) -> pd.Series:
    """Padroniza chaves: texto, sem espaços e sem zeros à esquerda."""
    return serie.astype("string").str.strip().str.lstrip("0")


def _data(serie: pd.Series) -> pd.Series:
    """Aceita dd-mm-aaaa (CSV da Silver) e aaaa-mm-dd (datas vindas do Databricks)."""
    texto = serie.astype("string").str.strip()
    br = pd.to_datetime(texto, format="%d-%m-%Y", errors="coerce")
    iso = pd.to_datetime(texto.str.slice(0, 10), format="%Y-%m-%d", errors="coerce")
    return br.fillna(iso)


def carregar_silver(pasta: str | Path) -> dict[str, pd.DataFrame]:
    """Lê os 6 CSVs, converte o texto 'null' em vazio e padroniza tipos."""
    pasta = Path(pasta)
    faltando = [nome for nome in ARQUIVOS.values() if not (pasta / nome).exists()]
    if faltando:
        raise FileNotFoundError(f"Arquivos da Silver não encontrados em {pasta}: {faltando}")

    d = {
        chave: pd.read_csv(pasta / nome, dtype=str, na_values=["null", "NULL", ""],
                           keep_default_na=False, encoding="utf-8-sig")
        for chave, nome in ARQUIVOS.items()
    }
    return padronizar_silver(d)


def padronizar_silver(d: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Padroniza chaves, datas e quantidades, venha a Silver de CSV ou do Databricks."""
    # O Databricks grafa "planejada"; os CSVs do dicionário, "planeada".
    d["remessas"] = d["remessas"].rename(
        columns={"data_planejada_entrega": "data_planeada_entrega"})
    for tabela in d.values():
        tabela.columns = [c.strip() for c in tabela.columns]
        for coluna in tabela.columns:
            if coluna.startswith("id_") or coluna in ("id_pedido_original", "codigo_produto_enviado"):
                tabela[coluna] = _id(tabela[coluna])

    d["pedidos"]["data_pedido"] = _data(d["pedidos"]["data_pedido"])
    d["remessas"]["data_planeada_entrega"] = _data(d["remessas"]["data_planeada_entrega"])
    d["remessas"]["data_real_entrega"] = _data(d["remessas"]["data_real_entrega"])
    d["itens_pedido"]["qtd"] = pd.to_numeric(d["itens_pedido"]["qtd"], errors="coerce")
    d["itens_remessa"]["qtd_enviada"] = pd.to_numeric(d["itens_remessa"]["qtd_enviada"], errors="coerce")
    for chave in ("id_item", "id_item_pedido_original"):
        for tabela in (d["itens_pedido"], d["itens_remessa"]):
            if chave in tabela.columns:
                tabela[chave] = _id(tabela[chave])
    return d


def filtrar_pedidos_do_dia(d: dict[str, pd.DataFrame], dia: pd.Timestamp,
                           ate: pd.Timestamp | None = None) -> dict[str, pd.DataFrame]:
    """Restringe a Silver aos pedidos feitos em um dia (ou de `dia` até `ate`) e às
    remessas ligadas a eles.

    Limita o volume de alertas (ex.: teste com 1 dia). Clientes e materiais ficam inteiros.
    """
    datas = d["pedidos"]["data_pedido"].dt.normalize()
    fim = (ate or dia).normalize()
    ids = set(d["pedidos"].loc[(datas >= dia.normalize()) & (datas <= fim), "id_pedido"])
    itens_remessa = d["itens_remessa"][d["itens_remessa"]["id_pedido_original"].isin(ids)]
    return {
        **d,
        "pedidos": d["pedidos"][d["pedidos"]["id_pedido"].isin(ids)],
        "itens_pedido": d["itens_pedido"][d["itens_pedido"]["id_pedido"].isin(ids)],
        "itens_remessa": itens_remessa,
        "remessas": d["remessas"][d["remessas"]["id_remessa"].isin(set(itens_remessa["id_remessa"]))],
    }


# ---------------------------------------------------------------- regras
def aplicar_regras(d: dict[str, pd.DataFrame], hoje: pd.Timestamp) -> pd.DataFrame:
    """Devolve um DataFrame com uma linha por (regra, pedido) encontrada."""
    pedidos, itens = d["pedidos"], d["itens_pedido"]
    remessas, itens_rem = d["remessas"], d["itens_remessa"]
    alertas: list[dict] = []

    def alerta(regra, id_pedido, detalhe):
        alertas.append({"regra": regra, "id_pedido": str(id_pedido or "—"), "detalhe": detalhe})

    # Liga cada remessa ao(s) pedido(s) de origem pelos itens enviados
    rem_ped = (itens_rem[["id_remessa", "id_pedido_original"]].dropna().drop_duplicates()
               .merge(remessas, on="id_remessa", how="left"))

    # 1 e 2: atraso na entrega
    for _, r in rem_ped.iterrows():
        prevista, real = r["data_planeada_entrega"], r["data_real_entrega"]
        if pd.isna(prevista):
            continue
        if pd.notna(real) and real > prevista:
            alerta("entrega_atrasada", r["id_pedido_original"],
                   f"Remessa {r['id_remessa']} entregue {(real - prevista).days} dia(s) após o previsto")
        elif pd.isna(real) and prevista < hoje:
            alerta("entrega_vencida", r["id_pedido_original"],
                   f"Remessa {r['id_remessa']} sem entrega; previsão vencida há {(hoje - prevista).days} dia(s)")

    # 3: pedido não processado e sem remessa
    com_remessa = set(rem_ped["id_pedido_original"])
    for _, p in pedidos.iterrows():
        if p.get("cod_situacao_pedido") == "A" and p["id_pedido"] not in com_remessa:
            alerta("pedido_parado", p["id_pedido"], "Pedido não processado e sem remessa")

    # 4: entrega parcial (itens não rejeitados com quantidade enviada menor)
    validos = itens[itens["cod_motivo_rejeicao"].isna()]
    enviados = (itens_rem.groupby(["id_pedido_original", "id_item_pedido_original"], as_index=False)
                ["qtd_enviada"].sum())
    comp = validos.merge(enviados, left_on=["id_pedido", "id_item"],
                         right_on=["id_pedido_original", "id_item_pedido_original"], how="left")
    comp = comp[comp["id_pedido"].isin(com_remessa)]
    for id_pedido, grupo in comp.groupby("id_pedido"):
        faltam = grupo[grupo["qtd_enviada"].fillna(0) < grupo["qtd"]]
        if len(faltam):
            itens_txt = ", ".join(f"item {i}" for i in faltam["id_item"])
            alerta("entrega_parcial", id_pedido, f"Enviado menos que o pedido: {itens_txt}")

    # 5: produto enviado diferente do pedido
    prod = itens_rem.merge(itens[["id_pedido", "id_item", "id_material"]],
                           left_on=["id_pedido_original", "id_item_pedido_original"],
                           right_on=["id_pedido", "id_item"], how="inner")
    for _, r in prod[prod["codigo_produto_enviado"] != prod["id_material"]].iterrows():
        alerta("produto_divergente", r["id_pedido_original"],
               f"Item {r['id_item_pedido_original']}: pedido {r['id_material']}, enviado {r['codigo_produto_enviado']}")

    # 6: item cancelado ou recusado (Z1 não informa a causa)
    for _, r in itens[itens["cod_motivo_rejeicao"].notna()].iterrows():
        alerta("item_rejeitado", r["id_pedido"],
               f"Item {r['id_item']} com motivo {r['cod_motivo_rejeicao']}; causa a confirmar")

    # 7: qualidade dos dados
    chaves_itens = set(zip(itens["id_pedido"], itens["id_item"]))
    for _, r in itens_rem.iterrows():
        if (r["id_pedido_original"], r["id_item_pedido_original"]) not in chaves_itens:
            alerta("inconsistencia_dados", r["id_pedido_original"],
                   f"Remessa {r['id_remessa']} envia item {r['id_item_pedido_original']} que não existe no pedido")
    for id_remessa in set(remessas["id_remessa"]) - set(itens_rem["id_remessa"]):
        alerta("inconsistencia_dados", None, f"Remessa {id_remessa} sem itens")
    cli = rem_ped.merge(pedidos[["id_pedido", "id_cliente"]], left_on="id_pedido_original",
                        right_on="id_pedido", how="inner", suffixes=("_remessa", "_pedido"))
    for _, r in cli[cli["id_cliente_remessa"] != cli["id_cliente_pedido"]].iterrows():
        alerta("inconsistencia_dados", r["id_pedido_original"],
               f"Remessa {r['id_remessa']} é do cliente {r['id_cliente_remessa']}, pedido do {r['id_cliente_pedido']}")

    tabela = pd.DataFrame(alertas, columns=["regra", "id_pedido", "detalhe"]).drop_duplicates()
    tabela["gravidade"] = tabela["regra"].map(lambda r: REGRAS[r][0])
    tabela["runbook"] = tabela["regra"].map(lambda r: REGRAS[r][1])
    return tabela.sort_values(["gravidade", "regra", "id_pedido"]).reset_index(drop=True)


# ---------------------------------------------------------------- planilha
def abrir_aba_plantao(sheet_id: str, arquivo_credenciais: str, aba: str = "Alertas"):
    """Abre (ou cria) a aba de alertas e garante todas as colunas do esquema."""
    import gspread
    from gspread.exceptions import WorksheetNotFound

    planilha = gspread.service_account(filename=arquivo_credenciais).open_by_key(sheet_id)
    try:
        folha = planilha.worksheet(aba)
    except WorksheetNotFound:
        folha = planilha.add_worksheet(title=aba, rows=1000, cols=len(COLUNAS_PLANTAO))
    garantir_cabecalho(folha)
    return folha


def garantir_cabecalho(folha) -> None:
    """Cria o cabeçalho ou acrescenta no fim as colunas que faltam (sem mover as antigas)."""
    atual = folha.row_values(1)
    if not atual:
        folha.update(range_name="A1", values=[COLUNAS_PLANTAO])
        return
    faltando = [c for c in COLUNAS_PLANTAO if c not in atual]
    if faltando:
        if hasattr(folha, "col_count") and folha.col_count < len(atual) + len(faltando):
            folha.add_cols(len(atual) + len(faltando) - folha.col_count)
        folha.update(range_name=_celula(1, len(atual) + 1), values=[faltando])


def _celula(linha: int, coluna: int) -> str:
    letras = ""
    while coluna:
        coluna, resto = divmod(coluna - 1, 26)
        letras = chr(65 + resto) + letras
    return f"{letras}{linha}"


def _proximo_numero(existentes: list[dict]) -> int:
    """Maior número A-XXXX já usado + 1 (as linhas do Forms usam FORM-XXXX)."""
    numeros = [int(str(r.get("alerta_id", ""))[2:]) for r in existentes
               if str(r.get("alerta_id", "")).startswith("A-") and str(r.get("alerta_id", ""))[2:].isdigit()]
    return max(numeros, default=0) + 1


# Tipo de reclamação do Forms -> regras do SAP do mesmo assunto (igual ao agente,
# integrations/plantao.py). Serve para escolher a qual alerta novo uma
# reclamação já aberta se junta quando o pedido tem mais de um alerta.
RELACIONADAS = {
    "reclamacao_atraso": {"entrega_atrasada", "entrega_vencida", "pedido_parado"},
    "reclamacao_nao_enviado": {"pedido_parado", "entrega_vencida"},
    "reclamacao_item_faltando": {"entrega_parcial"},
    "reclamacao_produto_errado": {"produto_divergente"},
    "reclamacao_cancelamento": {"item_rejeitado"},
    "reclamacao_cobranca": set(),
    "reclamacao_pedido_nao_encontrado": set(),
}


# Colunas do Forms que o caso (alerta) herda das reclamações vinculadas a ele.
CAMPOS_FORMS = ["tipo_contestacao", "recebida_em", "produto", "valor_informado",
                "triada_em", "tempo_triagem_min", "procede", "dias_atraso", "cupom"]
_JUNTAR = {"tipo_contestacao", "produto", "cupom"}       # acumula valores diferentes
_ORDEM_PROCEDE = {"sim": 0, "indeterminado": 1, "não": 2, "nao": 2}


def mesclar_forms(caso: dict, reclamacao: dict) -> dict[str, str]:
    """Valores das colunas do Forms que o caso deve receber da reclamação.

    tipo, produto e cupom acumulam ("Atraso; Cobrança"); datas, valor e tempo de
    triagem ficam com a primeira reclamação; procede fica com o mais forte (sim >
    indeterminado > não); dias_atraso com o maior. Devolve só o que muda.
    """
    novos = {}
    for campo in CAMPOS_FORMS:
        atual = str(caso.get(campo, "") or "").strip()
        valor = str(reclamacao.get(campo, "") or "").strip()
        if not valor:
            continue
        if campo in _JUNTAR:
            partes = [p.strip() for p in atual.split(";") if p.strip()]
            resultado = "; ".join(partes + [valor]) if valor not in partes else atual
        elif campo == "procede":
            resultado = min([v for v in (atual, valor) if v],
                            key=lambda v: _ORDEM_PROCEDE.get(v.lower(), 3))
        elif campo == "dias_atraso":
            try:
                resultado = str(max(int(float(atual or 0)), int(float(valor))))
            except ValueError:
                resultado = atual or valor
        else:
            resultado = atual or valor
        if resultado != atual:
            novos[campo] = resultado
    return novos


def limitar_alertas(alertas: pd.DataFrame, max_por_regra: int = 0, limiar_surto: int = 0) -> pd.DataFrame:
    """Evita saturar o plantão quando uma regra pega muitos pedidos de uma vez.

    - limiar_surto: se uma regra passar desse número de pedidos no ciclo, entra
      UM alerta P1 "surto_<regra>" com a contagem e exemplos (incidente em massa).
    - max_por_regra: no máximo N alertas individuais por regra (os primeiros).
    0 desliga cada um.
    """
    if alertas.empty:
        return alertas
    partes = []
    for regra, grupo in alertas.groupby("regra", sort=False):
        if limiar_surto and len(grupo) > limiar_surto:
            exemplos = ", ".join(str(p) for p in grupo["id_pedido"].head(5))
            partes.append(pd.DataFrame([{
                "regra": f"surto_{regra}", "id_pedido": "—",
                "detalhe": f"Surto: {len(grupo)} pedidos com {regra} no mesmo ciclo (ex.: {exemplos})",
                # surto de regra P2/P3 é incidente em massa (P1); de P4 (dados) sobe só para P2
                "gravidade": "P2" if REGRAS.get(regra, ("P2",))[0] == "P4" else "P1",
                "runbook": REGRAS.get(regra, ("", "RB-07"))[1],
            }]))
        partes.append(grupo.head(max_por_regra) if max_por_regra else grupo)
    return pd.concat(partes, ignore_index=True)


def _chave(regra: str, id_pedido: str, detalhe: str) -> tuple[str, str, str]:
    """Identidade de um alerta: regra + pedido + detalhe sem a contagem de dias.

    O detalhe muda todo dia ("vencida há 27 dia(s)", "há 28 dia(s)"), então os
    números de dias saem da comparação; itens diferentes ("Item 10", "Item 20")
    continuam sendo alertas diferentes.
    """
    if str(regra).startswith("surto_"):
        return (str(regra), "", "")  # um surto aberto por regra, o número de pedidos pode mudar
    return (str(regra), str(id_pedido), re.sub(r"\d+\s*dia\(s\)", "N dia(s)", str(detalhe)))


JANELA_SURTO_H = 12  # um surto resolvido só pode reabrir depois disso (novo incidente)


def _surto_encerrado_ha_tempo(r: dict) -> bool:
    """Surto resolvido há mais de JANELA_SURTO_H horas deixa abrir um surto novo da regra.

    Dentro da janela, o mesmo surto não reabre a cada ciclo só porque os pedidos
    afetados continuam na base (eles seguem nos alertas individuais).
    """
    if not str(r.get("regra", "")).startswith("surto_") or r.get("status") not in ("RESOLVIDO", "SEM_ACAO"):
        return False
    try:
        detectado = datetime.strptime(str(r.get("detectado_em", ""))[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=FUSO)
    except ValueError:
        return True
    return datetime.now(FUSO) - detectado > timedelta(hours=JANELA_SURTO_H)


def novos_alertas(alertas: pd.DataFrame, existentes: list[dict],
                  cabecalho: list[str] | None = None) -> list[dict]:
    """Filtra o que já está na planilha e devolve os registros novos (dicionários)."""
    ja = {_chave(r.get("regra", ""), r.get("id_pedido", ""), r.get("detalhe", ""))
          for r in existentes if str(r.get("origem", "SAP")) in ("SAP", "")
          and not _surto_encerrado_ha_tempo(r)}
    agora = datetime.now(FUSO).strftime("%Y-%m-%d %H:%M:%S")
    proximo = _proximo_numero(existentes)
    registros = []
    for _, a in alertas.iterrows():
        chave = _chave(a["regra"], a["id_pedido"], a["detalhe"])
        if chave in ja:
            continue
        ja.add(chave)
        registros.append({
            "alerta_id": f"A-{proximo + len(registros):04d}", "regra": a["regra"],
            "id_pedido": a["id_pedido"], "detalhe": a["detalhe"], "detectado_em": agora,
            "gravidade": a["gravidade"], "runbook": a["runbook"], "status": "ABERTO",
            "origem": "SAP", "reclamacoes_vinculadas": "",
            **_cupom_do_alerta(a["regra"], a["detalhe"]),
        })
    return registros


def _cupom_do_alerta(regra: str, detalhe: str) -> dict[str, str]:
    """Só entrega_atrasada (atraso confirmado com entrega feita) é elegível, como no agente."""
    if regra == "entrega_atrasada":
        dias = dias_no_detalhe(detalhe)
        if dias > 0:
            return {"cupom_elegivel": "ELEGIVEL", "cupom_faixa": faixa_cupom(dias), "dias_atraso": str(dias)}
    return {"cupom_elegivel": "NAO_ELEGIVEL"}


def absorver_reclamacoes(registros: list[dict], existentes: list[dict]) -> list[tuple[int, str]]:
    """Junta aos alertas novos as reclamações do Forms ABERTAS do mesmo pedido.

    A reclamação chegou antes do alerta (o cliente avisou primeiro). Ela passa a
    VINCULADO e o alerta ganha o ID dela (e das que já estavam ligadas a ela) em
    reclamacoes_vinculadas. Devolve [(linha_da_planilha, alerta_id)] a atualizar.
    """
    por_pedido: dict[str, list[dict]] = {}
    for r in registros:
        por_pedido.setdefault(str(r["id_pedido"]), []).append(r)
    mudancas = []
    for numero, e in enumerate(existentes, start=2):
        if str(e.get("origem", "")).upper() != "FORMS" or e.get("status") != "ABERTO":
            continue
        if e.get("regra") == "reclamacao_revisao_humana":
            continue  # pedido não confirmado pelo agente: o N1 confirma antes de juntar
        candidatos = por_pedido.get(str(e.get("id_pedido", "")))
        if not candidatos:
            continue
        relacionadas = RELACIONADAS.get(e.get("regra", ""), set())
        alvo = min(candidatos, key=lambda r: (r["regra"] not in relacionadas, r["gravidade"]))
        ids = [str(e.get("alerta_id", "")).strip()]
        ids += [v.strip() for v in str(e.get("reclamacoes_vinculadas", "")).split(",") if v.strip()]
        atuais = [v for v in alvo["reclamacoes_vinculadas"].split(", ") if v]
        alvo["reclamacoes_vinculadas"] = ", ".join(atuais + [i for i in ids if i and i not in atuais])
        alvo.update(mesclar_forms(alvo, e))  # o alerta herda os dados do Forms
        mudancas.append((numero, alvo["alerta_id"]))
    return mudancas


def limitar_novos(registros: list[dict], max_por_regra: int) -> list[dict]:
    """No máximo N alertas NOVOS por regra neste ciclo; o resto entra nos próximos.

    O limite vale depois de tirar o que já está na planilha: assim a fila anda
    (N por regra a cada ciclo) em vez de repetir sempre os mesmos N primeiros.
    Surtos não entram no limite. Os IDs são renumerados em sequência.
    """
    if not max_por_regra:
        return registros
    contagem: dict[str, int] = {}
    escolhidos = []
    for r in registros:
        regra = str(r.get("regra", ""))
        if not regra.startswith("surto_"):
            contagem[regra] = contagem.get(regra, 0) + 1
            if contagem[regra] > max_por_regra:
                continue
        escolhidos.append(r)
    if escolhidos:
        primeiro = int(re.sub(r"\D", "", escolhidos[0]["alerta_id"]) or 1)
        for i, r in enumerate(escolhidos):
            r["alerta_id"] = f"A-{primeiro + i:04d}"
    return escolhidos


def gravar_alertas(alertas: pd.DataFrame, folha, max_por_regra: int = 0) -> int:
    valores = folha.get_all_values()
    cabecalho = valores[0] if valores else COLUNAS_PLANTAO
    existentes = [dict(zip(cabecalho, linha + [""] * (len(cabecalho) - len(linha))))
                  for linha in valores[1:]]
    registros = limitar_novos(novos_alertas(alertas, existentes, cabecalho), max_por_regra)
    if not registros:
        return 0
    mudancas = absorver_reclamacoes(registros, existentes)
    folha.append_rows([[r.get(c, "") for c in cabecalho] for r in registros],
                      value_input_option="RAW")
    # Reclamações absorvidas: status VINCULADO e nota no detalhe
    col_status = cabecalho.index("status") + 1
    col_detalhe = cabecalho.index("detalhe") + 1
    for numero, alerta_id in mudancas:
        detalhe = existentes[numero - 2].get("detalhe", "")
        folha.update_cell(numero, col_status, "VINCULADO")
        folha.update_cell(numero, col_detalhe, f"{detalhe}. Vinculada a {alerta_id} (alerta do SAP detectado depois)")
    if mudancas:
        print(f"{len(mudancas)} reclamação(ões) do Forms vinculada(s) aos alertas novos.", flush=True)
    return len(registros)


if __name__ == "__main__":
    pasta = sys.argv[1] if len(sys.argv) > 1 else "data/entrada/silver"
    hoje = pd.Timestamp(sys.argv[2]) if len(sys.argv) > 2 else pd.Timestamp(datetime.now(FUSO).date())
    resultado = aplicar_regras(carregar_silver(pasta), hoje)
    print(resultado.to_string(index=False) if len(resultado) else "Nenhum alerta.")
