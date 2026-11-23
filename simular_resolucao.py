"""Simula o analista N1 resolvendo os casos abertos da aba Alertas, seguindo o runbook.

Para cada caso em andamento, escolhe uma causa realista do catálogo da regra
(o que costuma acontecer numa empresa de entregas), executa os passos do RB
e grava na planilha, como se o N1 tivesse feito:

  ABERTO -> RECONHECIDO (reconhecido_em = agora, responsável)
         -> RESOLVIDO   (resolvido_em = agora, causa com o que foi feito)
  e fecha junto as reclamações vinculadas ao caso.

Os horários são reais (o momento em que você roda), então o Painel mede o MTTA
e o MTTR de verdade: rode primeiro só o reconhecimento e, minutos depois, a
resolução, para ver os dois tempos separados.

Uso, na pasta noc-monitoramento:
    python simular_resolucao.py --simular              # mostra o que faria, sem gravar
    python simular_resolucao.py --etapa reconhecer     # só RECONHECIDO
    python simular_resolucao.py --etapa resolver       # resolve os reconhecidos
    python simular_resolucao.py                        # as duas coisas de uma vez
    python simular_resolucao.py --prioridades P2,P3 --resolver 70
        (resolve 70% dos casos; o resto fica em andamento, como num plantão real)

A escolha da causa é sorteada com semente fixa por caso (mesmo alerta, mesma causa).
Casos fechados pelo agente ("Agente IA") e os de revisão humana (RB-08, que dependem
do cliente) não são tocados.
"""

from __future__ import annotations

import argparse
import os
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from alertas import FUSO, _celula, abrir_aba_plantao

PASTA = Path(__file__).resolve().parent
EM_ANDAMENTO = {"ABERTO", "RECONHECIDO"}

# ---------------------------------------------------------------- catálogo
# regra -> [(peso, causa)]. {d} = data simulada (próximos dias), {faixa} = cupom.
# Mesmo catálogo do histórico (gerar_historico.py) + reclamações encaminhadas pelo agente
from gerar_historico import CAUSAS, CAUSAS_SURTO  # noqa: E402

CATALOGO: dict[str, list[tuple[int, str]]] = {
    **CAUSAS,
    "reclamacao_nao_enviado": [(100, "Pedido liberado para separação; remessa prevista para {d}")],
    "reclamacao_item_faltando": [(100, "Item faltante enviado em remessa complementar, previsão {d}")],
    "reclamacao_produto_errado": [(100, "Coleta reversa agendada e produto correto reenviado, previsão {d}")],
    "reclamacao_cancelamento": [(100, "Cancelamento confirmado e estorno aberto no Financeiro")],
    "reclamacao_cobranca": [(100, "Cobrança conferida; diferença estornada pelo Financeiro")],
    "reclamacao_atraso": [(100, "Transportadora acionada; nova previsão {d}")],
}
SURTO = CAUSAS_SURTO


def causa_para(linha: dict[str, str], sorteio: random.Random) -> str:
    regra = linha.get("regra", "")
    opcoes = SURTO if regra.startswith("surto_") else CATALOGO.get(regra)
    if not opcoes:
        return "Caso verificado e encaminhado à equipe responsável"
    pesos, textos = zip(*opcoes)
    texto = sorteio.choices(textos, weights=pesos, k=1)[0]
    previsao = date.today() + timedelta(days=sorteio.randint(2, 5))
    return texto.format(d=previsao.strftime("%d/%m"), faixa=linha.get("cupom_faixa") or "pela política")


# ---------------------------------------------------------------- execução
def analistas(folha) -> list[str]:
    try:
        valores = folha.spreadsheet.worksheet("Equipe").get_all_values()
    except Exception:  # noqa: BLE001
        return []
    cab = [c.strip().lower() for c in valores[0]] if valores else []
    if "nome" not in cab:
        return []
    i_nome = cab.index("nome")
    i_nivel = cab.index("nivel") if "nivel" in cab else -1
    i_ativo = cab.index("ativo") if "ativo" in cab else -1
    return [l[i_nome] for l in valores[1:] if l[i_nome]
            and (i_nivel < 0 or l[i_nivel].strip().upper() == "N1")
            and (i_ativo < 0 or l[i_ativo].strip().upper() not in ("NÃO", "NAO", "N"))]


def main() -> None:
    parser = argparse.ArgumentParser(description="Simula o N1 resolvendo os casos pelo runbook")
    parser.add_argument("--etapa", choices=["reconhecer", "resolver", "tudo"], default="tudo")
    parser.add_argument("--prioridades", default="P1,P2,P3", help="ex.: P2,P3")
    parser.add_argument("--resolver", type=int, default=100, help="%% dos casos que serão resolvidos")
    parser.add_argument("--simular", action="store_true", help="mostra o que faria, sem gravar")
    args = parser.parse_args()

    load_dotenv(PASTA / ".env")
    cred = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    cred = cred if os.path.isabs(cred) else str(PASTA / cred)
    folha = abrir_aba_plantao(os.environ["PLANTAO_SHEET_ID"], cred)
    valores = folha.get_all_values()
    cab = valores[0]
    linhas = [dict(zip(cab, v + [""] * (len(cab) - len(v)))) for v in valores[1:]]
    por_id = {l.get("alerta_id", ""): n for n, l in enumerate(linhas, start=2)}
    prioridades = {p.strip().upper() for p in args.prioridades.split(",")}
    equipe = analistas(folha) or ["N1 Simulado"]
    agora = datetime.now(FUSO).strftime("%Y-%m-%d %H:%M:%S")

    mudancas: list[tuple[int, str, str]] = []
    resumo = []
    for n, l in enumerate(linhas, start=2):
        if l.get("status") not in EM_ANDAMENTO or l.get("gravidade") not in prioridades:
            continue
        if l.get("responsavel") == "Agente IA" or l.get("runbook") == "RB-08":
            continue
        sorteio = random.Random(l.get("alerta_id", str(n)))
        responsavel = l.get("responsavel") or sorteio.choice(equipe)
        acao = ""
        if l["status"] == "ABERTO" and args.etapa in ("reconhecer", "tudo"):
            mudancas += [(n, "status", "RECONHECIDO"), (n, "reconhecido_em", agora), (n, "responsavel", responsavel)]
            l["status"], acao = "RECONHECIDO", "reconhecido"
        resolve = sorteio.randint(1, 100) <= args.resolver
        if l["status"] == "RECONHECIDO" and resolve and args.etapa in ("resolver", "tudo"):
            causa = causa_para(l, sorteio)
            mudancas += [(n, "status", "RESOLVIDO"), (n, "resolvido_em", agora), (n, "causa", causa)]
            if not l.get("reconhecido_em") and acao != "reconhecido":
                mudancas.append((n, "reconhecido_em", agora))
            if not l.get("responsavel"):
                mudancas.append((n, "responsavel", responsavel))
            acao = "resolvido"
            # reclamações vinculadas fecham junto (o onEdit da planilha não roda em gravação pela API)
            for rid in [v.strip() for v in l.get("reclamacoes_vinculadas", "").split(",") if v.strip()]:
                m = por_id.get(rid)
                if m and linhas[m - 2].get("status") != "RESOLVIDO":
                    mudancas += [(m, "status", "RESOLVIDO"), (m, "resolvido_em", agora),
                                 (m, "causa", f"Resolvida com o caso {l['alerta_id']}: {causa}")]
                    if not linhas[m - 2].get("reconhecido_em"):
                        mudancas.append((m, "reconhecido_em", agora))
        if acao:
            resumo.append((l.get("alerta_id"), l.get("gravidade"), l.get("regra"), acao, responsavel))

    for r in resumo:
        print(f"{r[0]:<22} {r[1]}  {r[2]:<26} {r[3]:<12} {r[4]}")
    print(f"{len(resumo)} caso(s) tratados.")
    if args.simular or not mudancas:
        print("(nada gravado)" if args.simular else "Nada a fazer.")
        return
    folha.batch_update([{"range": _celula(n, cab.index(c) + 1), "values": [[v]]}
                        for n, c, v in mudancas if c in cab], value_input_option="RAW")
    print(f"Gravado às {agora}. O Painel já mostra MTTA, MTTR e prazos; o Apps Script manda os e-mails no próximo ciclo.")


if __name__ == "__main__":
    main()
