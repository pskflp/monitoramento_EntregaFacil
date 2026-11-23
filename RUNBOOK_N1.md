# Runbook N1 · Plantão de pedidos e entregas

## Como usar este runbook no plantão

Cada linha da aba Alertas traz o código do procedimento (RB-01 a RB-10) na coluna
`runbook`. O analista N1 segue os passos do RB indicado e registra o que fez na
coluna `causa`. Ciclo de status do caso:

- ABERTO: caso novo, ainda sem ninguém trabalhando.
- RECONHECIDO: o analista assumiu (grava `reconhecido_em`, para o MTTA).
- AGUARDANDO_CLIENTE: somente quando é preciso confirmar dados com o cliente (RB-08).
  Pausa lembretes e escalonamento.
- RESOLVIDO: causa tratada e registrada (grava `resolvido_em`, para o MTTR). Fecha
  junto as reclamações vinculadas ao caso.
- VINCULADO: reclamação do Forms ligada a um caso aberto do mesmo pedido; segue o caso.
- SEM_ACAO: reclamação improcedente (o pedido está no prazo); não há o que tratar.

Prazos do SLA (resposta / resolução): P1 15 min / 4 h, 24x7; P2 30 min / 1 dia útil;
P3 2 h / 2 dias úteis; P4 1 dia útil / 5 dias úteis. Horário comercial: segunda a
sexta, das 8h às 18h.

## Ação simulada nos alertas do SAP

Alertas gerados pelo monitor do SAP não têm contato do cliente. No MVP, o analista
simula a ação do procedimento e a registra na coluna `causa`, começando por
"Ação simulada:". Exemplo: "Ação simulada: transportadora consultada, nova previsão
21/11". Essa causa também é o texto enviado ao cliente quando uma reclamação do Forms
está vinculada ao caso.

## Comunicação com o cliente

Só reclamações do Forms têm e-mail de contato. A planilha envia o e-mail sozinha
conforme o status da linha FORM: RESOLVIDO (o que foi feito), AGUARDANDO_CLIENTE
(pedido de dados) e SEM_ACAO (pedido no prazo). O e-mail do cliente nunca é copiado
para a aba Alertas. O cupom é entregue pelo agente de triagem, não pelo plantão.

## Elegibilidade a cupom no plantão

A coluna `cupom_elegivel` diz se o caso dá direito a compensação, seguindo a
política de compensação do atendimento. O plantão não escolhe código nem
percentual: a entrega do cupom é do agente de triagem.

- ELEGIVEL: atraso confirmado com a entrega já realizada. Vale para o alerta
  entrega_atrasada e para a reclamação de atraso procedente com dias de atraso
  maior que zero.
- NAO_ELEGIVEL: entrega vencida e pedido parado (o atraso ainda é indeterminado;
  quando a entrega acontecer, o monitor abre entrega_atrasada, que é elegível);
  produto divergente, itens faltando, item rejeitado e cobrança (o tratamento é
  troca, reenvio ou estorno); inconsistência de dados; pedido não confirmado;
  reclamação improcedente; linha de surto (cada cliente é avaliado no próprio caso).

A planilha preenche a coluna quando ela está vazia. O analista pode corrigir à mão
depois de confirmar os dados com o cliente.

## Reclamação de um caso já resolvido

Se a reclamação do Forms chega para um pedido cujo caso do mesmo assunto já está
RESOLVIDO, ela entra como RESOLVIDO na hora: herda a causa ("Já resolvido no caso
A-0070: ...") e a elegibilidade a cupom do caso, e o cliente recebe o e-mail de
resolução. Se o assunto for diferente, ela abre um caso novo.

## Surto de pedidos (P1)

Um pedido atrasado é P2. Muitos pedidos com o mesmo problema no mesmo ciclo (mais de
20; 5 na demo) viram um único alerta surto_<regra>, P1, com a contagem e até 5 pedidos
de exemplo. O problema passou a ser da operação, não do pedido.

Automático: o monitor abre o P1 e continua trazendo os pedidos um a um (10 por regra a
cada ciclo); o Apps Script avisa o N1 na hora por e-mail e Chat, repete a cada 5 min e
escala ao N2 em 15 min sem RECONHECIDO (24x7); o agente atende os clientes que
reclamarem e entrega o cupom pela política.

1. Até 15 min: RECONHECIDO no surto (MTTA; para o escalonamento).
2. Até 60 min: filtrar a aba Alertas pela regra e pelo horário e achar o que os pedidos
   têm em comum (dia do pedido, remessa, material, cliente). Separar causa externa
   (transportadora, centro de distribuição) de interna (sistema, carga da Silver).
3. Acionar o dono: externa → Logística; interna → N2/Dados. Registrar na `causa`.
4. Avisar o Atendimento do volume e do tipo de reclamação esperado (o cupom é automático).
5. Até 4 h: RESOLVIDO no surto; depois filtrar os pedidos da regra e colar RESOLVIDO em
   todos de uma vez (o Apps Script carimba cada um, fecha as vinculadas e manda o e-mail).
6. Até 2 dias úteis: pós-incidente (impacto, linha do tempo, causa, o que muda).

Exemplo de causa: "Incidente: transportadora sem coleta desde 18/10; Logística acionada".

## RB-01 · Entrega atrasada

Gatilho: remessa entregue depois da data prevista (regra entrega_atrasada), ou
reclamação de atraso confirmada pelo agente. Prioridade P2. Cupom: ELEGIVEL, com a faixa
em `cupom_faixa` (preenchido pelo monitor). 31% dos alertas da base real.

A entrega já aconteceu: o trabalho é registrar o motivo e olhar o padrão. A compensação é
automática: quem entrega o cupom ao cliente é o agente.

1. Reconhecer e ver os dias de atraso e se há reclamação vinculada.
2. Diagnosticar: transportadora, separação no estoque ou previsão cadastrada errada.
3. Três ou mais casos com o mesmo motivo no dia: avisar Logística (ou já é surto P1).
4. Fechar: RESOLVIDO com o motivo. Não enviar cupom.

Pode automatizar depois: fechar sozinho quando não houver reclamação em 1 dia.

Exemplo de causa: "Ação simulada: atraso da transportadora na rota Sul; entrega concluída
em 12/11".

## RB-02 · Entrega vencida

Gatilho: remessa sem entrega e previsão já vencida (regra entrega_vencida).
Prioridade P2. Cupom: NAO_ELEGIVEL até a entrega acontecer. 16% dos alertas.

1. Reconhecer e ver há quantos dias a previsão venceu.
2. Ação simulada: pedir nova previsão à transportadora e registrar a data.
3. Escalar: Logística (N2) sem previsão em 4 h, ou atraso acima de 15 dias (reenvio ou
   cancelamento).
4. Fechar: RESOLVIDO com a nova previsão. Quando a entrega acontecer, o monitor abre o
   RB-01 e o caso fica elegível a cupom.

Pode automatizar depois: pedir a nova previsão por integração com a transportadora.

Exemplo de causa: "Ação simulada: transportadora informou nova previsão 25/11".

## RB-03 · Pedido parado

Gatilho: pedido não processado e sem remessa criada (regra pedido_parado), ou
reclamação de pedido não enviado. Prioridade P2. Cupom: NAO_ELEGIVEL.

1. Reconhecer o caso.
2. Verificar no SAP a situação do pedido e se há remessa para ele.
3. Diagnosticar: falta de estoque, bloqueio de faturamento ou erro de integração.
4. Ação simulada: liberar o pedido para separação e registrar a data prevista da
   remessa.
5. Escalar: N2 Dados se for erro de integração; Logística se for estoque.
6. Fechar: RESOLVIDO quando a remessa for criada.

Exemplo de causa: "Ação simulada: pedido liberado após reposição de estoque; remessa
prevista para 20/11".

## RB-04 · Entrega parcial

Gatilho: quantidade enviada menor que a pedida (regra entrega_parcial), ou
reclamação de itens faltando. Prioridade P3. Cupom: NAO_ELEGIVEL.

1. Reconhecer o caso.
2. Verificar quais itens faltam comparando itens_pedido e itens_remessa.
3. Diagnosticar: item em outra remessa, falta de estoque ou erro de separação.
4. Ação simulada: programar o envio do restante ou registrar a remessa que já leva o
   item.
5. Escalar: Logística se o item não tiver estoque.
6. Fechar: RESOLVIDO com a remessa complementar.

Exemplo de causa: "Ação simulada: item 10 segue na remessa complementar de 21/11".

## RB-05 · Produto divergente

Gatilho: a remessa leva um material diferente do pedido (regra produto_divergente),
ou reclamação de produto errado. Prioridade P2. Cupom: NAO_ELEGIVEL.

1. Reconhecer o caso.
2. Verificar o material pedido e o material enviado no SAP.
3. Diagnosticar: erro de separação ou cadastro de material trocado.
4. Ação simulada: abrir coleta do produto errado e envio do correto, pela equipe de
   Trocas e Devoluções.
5. Escalar: N2 Dados se o cadastro do material estiver errado.
6. Fechar: RESOLVIDO com a troca programada.

Exemplo de causa: "Ação simulada: coleta agendada e reenvio do produto correto".

## RB-06 · Item rejeitado

Gatilho: item do pedido com motivo de rejeição (regra item_rejeitado), ou reclamação
de cancelamento. Prioridade P3. Cupom: NAO_ELEGIVEL. Regra mais frequente da base real
(34% dos alertas).

1. Reconhecer e ver o motivo de rejeição e se o resto do pedido foi entregue.
2. Diagnosticar: cancelamento pelo cliente, falta definitiva de estoque ou bloqueio
   comercial.
3. Vários itens com o mesmo motivo no dia: tratar como um só problema e citar o motivo
   na causa de todos.
4. Ação simulada: estorno do item ao Financeiro (com reclamação: ligar ao RB-09).
5. Escalar: Financeiro se o estorno não for confirmado em 1 dia útil.
6. Fechar: RESOLVIDO com o estorno encaminhado.

Pode automatizar depois: agrupar por motivo de rejeição e abrir um caso por motivo.

Exemplo de causa: "Ação simulada: item 20 cancelado por falta de estoque; estorno
encaminhado ao Financeiro".

## RB-07 · Inconsistência de dados

Gatilho: remessa com item que não existe no pedido, ou datas incoerentes (regra
inconsistencia_dados). Prioridade P4. Cupom: NAO_ELEGIVEL.

1. Reconhecer o caso.
2. Verificar o registro na Silver e na tabela de origem do SAP.
3. Diagnosticar: erro na carga, chave trocada ou lançamento manual no SAP.
4. Ação simulada: abrir chamado para o time de Dados com o pedido e a remessa.
5. Escalar: N2 Dados (Engenharia de Dados).
6. Fechar: RESOLVIDO quando a correção for carregada na Silver.

Exemplo de causa: "Ação simulada: chamado aberto para Dados; remessa corrigida na
próxima carga".

## RB-08 · Revisão humana e confirmação de dados

Gatilho: reclamação do Forms que o agente não conseguiu confirmar (pedido inexistente
ou nome diferente do cadastro, tipo não reconhecido, relato que não bate com a
Gold ou atraso ainda sem data de entrega). Prioridade P4. Cupom: NAO_ELEGIVEL até
a confirmação. A linha não se junta sozinha a outro caso: se o pedido informado
tiver caso aberto, o detalhe aponta qual, e o N1 junta depois de confirmar.

1. Reconhecer o caso.
2. Verificar o pedido e o nome informados no Forms contra a Gold.
3. Se faltar dado do cliente, mudar para AGUARDANDO_CLIENTE: o cliente recebe o
   pedido de confirmação por e-mail. Quando ele responder, voltar para RECONHECIDO.
4. Confirmado o pedido, seguir o RB do assunto (cobrança e cancelamento: RB-09).
5. Atraso confirmado depois da conferência: registrar a faixa em `cupom_faixa`. O N1
   não envia cupom; a entrega é do agente.
6. Fechar: RESOLVIDO com o resultado da conferência.

Exemplo de causa: "Cliente confirmou o pedido 5004581; caso tratado pelo RB-02".

## RB-09 · Encaminhamento ao Financeiro (cobrança ou cancelamento)

Gatilho: reclamação de cobrança diferente, cancelamento ou reembolso que o agente
Entrega Fácil confirmou na Gold e encaminhou ao Financeiro (coluna `equipe`).
Prioridade P2 (cobrança) ou P3 (cancelamento). Cupom: NAO_ELEGIVEL.

1. Reconhecer o caso.
2. Conferir na Gold o pedido e a situação (a Gold não tem valores pagos).
3. Ação simulada: abrir a solicitação ao Financeiro com o ID do pedido e o tipo.
4. Se houver alerta item_rejeitado do mesmo pedido, juntar e citar na causa.
5. Escalar: Financeiro (N2) se não houver retorno no prazo do SLA.
6. Fechar: RESOLVIDO com o encaminhamento feito.

Exemplo de causa: "Ação simulada: solicitação de estorno aberta no Financeiro para o pedido 5000200".

## RB-10 · Monitor parado (P1)

Gatilho: a aba Saude ficou mais de 10 minutos sem ciclo novo (o monitor deveria
gravar um a cada 2 min). O Apps Script abre o caso S-xxxx, P1, e o fecha sozinho
quando o monitor volta. Disponibilidade do monitor = 1 − minutos parado ÷ minutos
observados, no Painel (24 h e 7 dias; meta 99,5%).

1. Reconhecer em até 15 min.
2. Ver na aba Saude o último ciclo e se houve FALHA (erro do Databricks) antes da parada.
3. Computador desligado ou reiniciado: religar e abrir o `rodar_monitor_loop.bat`.
   Erro do Databricks (token, warehouse desligado): acionar N2/Dados.
4. Conferir que os ciclos voltaram (Minutos sem ciclo < 4 no Painel). O caso fecha sozinho.
5. Escrever na causa o motivo da parada.

Exemplo de causa: "Computador do monitor reiniciou por atualização do Windows;
monitor reiniciado e ciclos normalizados".

## Causas comuns e como se resolvem

Cada regra tem poucas causas que se repetem; a causa indica a solução e o dono. Os
pesos são a suposição usada pelo simulador na demo, não medidas dos dados.

| Regra | Causa (peso na simulação) | Como se resolve | Dono |
|---|---|---|---|
| RB-01 Entrega atrasada | Transportadora atrasou na rota (40%) | Registrar a ocorrência e cobrar o prazo da transportadora | Logística |
| | Separação atrasada no CD por pico (25%) | Turno extra; prioridade aos mais antigos | Operação do CD |
| | Prazo cadastrado curto para a região (20%) | Revisar a tabela de prazos por CEP | Logística |
| | Destinatário ausente, entregue na reentrega (15%) | Registrar | N1 |
| RB-02 Entrega vencida | Carga parada no hub (40%) | Reagendar coleta, nova previsão | Logística |
| | Endereço incompleto (25%) | Contatar o cliente, corrigir e reenviar | N1 + Atendimento |
| | Extravio (20%) | Sinistro e reenvio expresso | Logística |
| | Devolvido após 3 tentativas (15%) | Agendar reenvio | Logística |
| RB-03 Pedido parado | Falta de estoque (40%) | Aguardar reposição ou oferecer cancelamento | Estoque |
| | Falha de integração com o SAP (30%) | Reprocessar o pedido | N2 / Dados |
| | Pagamento em análise antifraude (20%) | Liberar após a análise | Financeiro |
| | Pedido sem endereço válido (10%) | Contatar o cliente e liberar | N1 + Atendimento |
| RB-04 Entrega parcial | Item em remessa complementar (50%) | Informar a previsão | N1 |
| | Erro de separação (30%) | Complemento por entrega expressa | Operação do CD |
| | Item sem estoque (20%) | Estorno parcial | Financeiro |
| RB-05 Produto divergente | Erro de separação (70%) | Coleta reversa e reenvio | Trocas e Devoluções |
| | Cadastro de material trocado (30%) | Corrigir o cadastro e reenviar | Dados |
| RB-06 Item rejeitado | Cancelamento pelo cliente (40%) | Estorno do item | Financeiro |
| | Ruptura de estoque (35%) | Estorno; o resto do pedido segue | Financeiro |
| | Preço divergente / bloqueio comercial (25%) | Revisão comercial libera | Comercial |
| Surto P1 | Transportadora com coleta suspensa (50%) | Transportadora reserva e redistribuição | Logística |
| | Pico de pedidos no CD (30%) | Turno extra, mais antigos primeiro | Operação do CD |
| | Falha de integração SAP × transportadora (20%) | Reprocessar remessas | N2 / Dados |

### Simular a resolução (demo)

`simular_resolucao.py` faz o papel do N1 na planilha real, com horários reais:

1. `simular_reconhecer.bat`: tudo o que está ABERTO vira RECONHECIDO.
2. Minutos depois, `simular_resolver.bat`: 70% vira RESOLVIDO com a causa sorteada da
   tabela ("Ação simulada (RB-xx): …"); o resto segue em andamento. As reclamações
   vinculadas fecham junto e o Apps Script manda o e-mail ao cliente.

Não toca em revisão humana (RB-08) nem em casos fechados pelo agente.

## Casos vindos do agente Entrega Fácil

O agente atende o cliente no chat, confere o Forms com a Gold e registra o resultado
em `atendimentos.csv`. A ponte (`ponte_agente.py`) leva cada atendimento para a aba
Alertas com `origem = Forms` e a coluna `equipe` do agente.

| Resultado do agente | Status no plantão | Runbook | O que o N1 faz |
|---|---|---|---|
| Atraso confirmado, cupom entregue | RESOLVIDO, responsável "Agente IA" (ou VINCULADO ao alerta do SAP) | RB-01 | Nada pelo cliente; trata o alerta do SAP se houver |
| Atraso não confirmado | SEM_ACAO, "Agente IA" | RB-01 | Nada |
| Atraso sem data de entrega | ABERTO | RB-02 | Segue o RB-02 |
| Pedido não enviado | ABERTO (Logística) | RB-03 | Segue o RB-03 |
| Itens faltando | ABERTO (Logística) | RB-04 | Segue o RB-04 |
| Produto errado | ABERTO (Trocas e Devoluções) | RB-05 | Segue o RB-05 |
| Cobrança / cancelamento | ABERTO (Financeiro) | RB-09 | Segue o RB-09 |
| Pedido não encontrado ou revisão humana | ABERTO P4 | RB-08 | Confirma os dados com o cliente |

Linhas fechadas pelo agente não geram aviso ao plantão nem e-mail ao cliente: ele já
recebeu a resposta no chat. Quando o pedido já tem caso aberto no SAP do mesmo
assunto, a reclamação entra VINCULADO e o caso herda tipo, cupom e dias de atraso.
Se o caso do mesmo assunto já estava resolvido, a reclamação entra RESOLVIDO com a
mesma causa.

Contato do cliente: o agente apaga a resposta do Forms quando conclui o atendimento.
O script `apps_script/contatos_forms.gs` (instalado no formulário) guarda o e-mail e o
primeiro nome numa aba Contatos no momento do envio. Assim os casos encaminhados e as
revisões humanas recebem o e-mail de retorno (AGUARDANDO_CLIENTE, RESOLVIDO, SEM_ACAO).
Casos fechados pelo agente ("Agente IA") não recebem: o cliente já teve a resposta no
chat.

## Faixas de cupom (política do agente)

O plantão não entrega cupom: indica a faixa em `cupom_faixa` para o alerta
entrega_atrasada e registra em `cupom` o código que o agente entregou. A tabela é a
mesma de `tools/politica_cupons.py` do agente; a ponte confere as duas ao iniciar.

| Atraso confirmado | Código | Benefício |
|---|---|---|
| 1 dia | EF-FRETE | frete grátis |
| 2 dias | EF-CORTESIA5 | 5% |
| 3 a 4 dias | EF-GARANTIA10 | 10% |
| 5 a 7 dias | EF-COMPROMISSO15 | 15% |
| 8 a 14 dias | EF-PRIORIDADE20 | 20% + prioridade |
| 15 dias ou mais | EF-PREMIUM25 | 25% ou reembolso pela revisão humana |

