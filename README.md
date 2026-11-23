# Monitoramento NOC · PagFácil

Monitor de alertas sobre os pedidos do SAP (camada Silver da Rebeca). Projeto independente: não usa o agente, o ADK nem o Gemini.

**O que faz:** a cada ciclo, aplica 7 regras nos 6 CSVs da Silver e grava um alerta por pedido na aba `Alertas` da planilha de plantão, sem repetir os que já existem. O Apps Script da planilha (`apps_script/plantao.gs`) avisa o plantão por e-mail, carimba os horários e escala para o N2.

## Arquivos

| Arquivo | Para quê |
|---|---|
| `monitor.py` | O programa: roda em loop e grava na planilha |
| `alertas.py` | Regras de alerta, limite/surto, faixa de cupom e gravação na planilha |
| `databricks_silver.py` | Lê as 6 tabelas da Silver direto do Databricks |
| `ponte_agente.py` | Leva os atendimentos do agente Entrega Fácil (Nathália) para a aba Alertas |
| `apps_script/plantao.gs` | Script da planilha: avisos por prioridade, distribuição, escalonamento, e-mail ao cliente |
| `apps_script/contatos_forms.gs` | Script do formulário: guarda o e-mail do cliente na aba Contatos (o agente apaga a resposta) |
| `apps_script/AVISOS_PLANTAO.md` | Guia de configuração do Apps Script |
| `RUNBOOK_N1.md` | Procedimentos RB-01 a RB-09 e casos vindos do agente |
| `*.bat` | Atalhos de Windows: demo, loop, um dia, base inteira, ponte |
| `data/amostras/silver/` | Silver **sintética** com um caso de cada regra |
| `.env.example` | Modelo de configuração |

## Instalação (Windows, uma vez)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Preencha o `.env` e salve o `service_account.json` nesta pasta (os dois ficam fora do Git).

## Uso

```
python monitor.py --uma-vez --hoje 2026-11-18                     # um ciclo, mostra a tabela
python monitor.py --intervalo 120 --max-por-regra 10 --limiar-surto 20   # loop sem saturar o plantão
python ponte_agente.py --intervalo 20                             # integração com o agente
python ponte_agente.py --uma-vez --simular                        # mostra o que gravaria
```

- `--hoje`: data de referência para as regras de atraso (os dados do SAP são históricos).
- `--dia-pedido`: só os pedidos feitos nesse dia.
- `--max-por-regra`: no máximo N alertas NOVOS por regra a cada ciclo; o resto entra nos ciclos seguintes (a fila anda, nada se perde).
- `--limiar-surto`: regra com mais de N pedidos no ciclo abre 1 alerta P1 de surto.
- `rodar_monitor_loop.bat` = operação (base inteira, 10 por regra, surto acima de 20); `rodar_monitor_demo.bat` = demo (um dia, 2 por regra, surto acima de 5).

## Integração com o agente Entrega Fácil

O agente (`Entrega-facil-agent-main`) atende o cliente no chat, confere o Forms com a
Gold, entrega o cupom e grava `data/saida/atendimentos.csv`. A ponte lê esse arquivo
(nada no código do agente muda) e grava na aba Alertas:

| Atendimento do agente | Linha no plantão |
|---|---|
| Atraso confirmado + cupom | RESOLVIDO por "Agente IA" (ou VINCULADO ao alerta do SAP do pedido) |
| Atraso não confirmado | SEM_ACAO por "Agente IA" |
| Encaminhado a Logística, Financeiro ou Trocas | ABERTO no RB da equipe (ou VINCULADO) |
| Revisão humana | ABERTO P4, RB-08, com o pedido informado no Forms |

O ID da reclamação é o do agente (`FORM-<carimbo do Forms>`), e a ponte não repete o que
já está na aba. As faixas de cupom do plantão (`FAIXAS_CUPOM`) são conferidas com
`tools/politica_cupons.py` do agente ao iniciar.

## Regras

| Regra | Condição (colunas da Silver) | Gravidade | Runbook |
|---|---|---|---|
| `entrega_atrasada` | `data_real_entrega` > `data_planeada_entrega` | P2 | RB-01 |
| `entrega_vencida` | sem `data_real_entrega` e previsão anterior a hoje | P2 | RB-02 |
| `pedido_parado` | `cod_situacao_pedido` = A e nenhuma remessa | P2 | RB-03 |
| `entrega_parcial` | soma de `qtd_enviada` menor que `qtd` (itens não rejeitados) | P3 | RB-04 |
| `produto_divergente` | `codigo_produto_enviado` diferente do `id_material` do item | P2 | RB-05 |
| `item_rejeitado` | `cod_motivo_rejeicao` preenchido (Z1); causa não informada | P3 | RB-06 |
| `inconsistencia_dados` | item enviado fora do pedido, remessa sem itens, remessa de outro cliente | P4 | RB-07 |

Remessas são ligadas aos pedidos por `itens_remessa.id_pedido_original`. Chaves sem zeros à esquerda; o texto `null` vira vazio; datas no formato `dd-mm-aaaa`.

## A aba `Alertas`: duas origens, um lugar só

A aba recebe **alertas do SAP** (este monitor, `origem = SAP`) e **reclamações** atendidas pelo agente Entrega Fácil (via `ponte_agente.py`, `origem = Forms`). O esquema tem 29 colunas; o que faltar é acrescentado no fim.

| Colunas | Quem preenche |
|---|---|
| 1 a 8: `alerta_id`, `regra`, `id_pedido`, `detalhe`, `detectado_em`, `gravidade`, `runbook`, `status` | Monitor (SAP) ou agente (Forms) |
| 9 a 14: `reconhecido_em`, `resolvido_em`, `responsavel`, `causa`, `avisado_em`, `escalado_em` | Apps Script e N1 |
| 15: `origem` | SAP ou Forms |
| 16 a 26: `id_reclamacao`, `recebida_em`, `tipo_contestacao`, `produto`, `valor_informado`, `triada_em`, `tempo_triagem_min`, `procede`, `dias_atraso`, `cupom`, `reclamacoes_vinculadas` | Ponte do agente e vínculo |
| 27 a 29: `cupom_elegivel`, `cupom_faixa`, `equipe` | Monitor/Apps Script (elegibilidade e faixa) e agente (equipe) |

O Apps Script e o painel localizam as colunas pelo nome: pode reordenar, não renomear.

**Status**

| Status | Significado | Gera e-mail? |
|---|---|---|
| `ABERTO` | Precisa do N1 | Sim |
| `RECONHECIDO` / `RESOLVIDO` | Mudado pelo N1; o Apps Script grava o horário | — |
| `VINCULADO` | Reclamação anexada a um alerta do SAP aberto, do mesmo pedido e do mesmo assunto; o N1 trata pelo alerta | Não |
| `SEM_ACAO` | Reclamação improcedente (ex.: atraso alegado, pedido no prazo); o agente já respondeu | Não |

`AGUARDANDO_CLIENTE` (só RB-08) pausa lembretes; a função `instalar()` do Apps Script cria a lista de status.

**Vínculo entre SAP e Forms:** quando o cliente reclama de um pedido que já tem alerta aberto do mesmo assunto (atraso com `entrega_atrasada`, item faltando com `entrega_parcial`, produto errado com `produto_divergente`, cancelamento com `item_rejeitado`), a reclamação entra como `VINCULADO` e o ID dela aparece em `reclamacoes_vinculadas` do alerta. O N1 vê que a falha já chegou ao cliente.

## Métricas para o dashboard (Maria Eduarda)

| Métrica | Como calcular na aba |
|---|---|
| MTTR | média de `resolvido_em` − `detectado_em`, linhas `RESOLVIDO`; separável por `origem` e `gravidade` |
| MTTA (tempo até reconhecer) | média de `reconhecido_em` − `detectado_em` |
| % dentro do SLA | linhas cujo tempo de resolução cabe no prazo da `gravidade` |
| Alertas por regra | contagem de `regra` onde `origem = SAP` |
| Reclamações por tipo | contagem de `tipo_contestacao` onde `origem = Forms` |
| % de reclamações procedentes | `procede = sim` ÷ reclamações com `procede` definido |
| Tempo até a triagem | média de `tempo_triagem_min` (do envio no Forms até a triagem do agente) |
| Atraso médio confirmado | média de `dias_atraso` |
| Reclamações sobre falhas já detectadas | linhas `VINCULADO` ÷ reclamações; mostra o valor do monitoramento proativo |
| Custo de compensação | contagem de `cupom` preenchido |

Para reclamações, `detectado_em` é o momento em que o cliente enviou o Forms: o MTTR mede o tempo que o cliente esperou.
