/**
 * Apps Script da planilha de plantão (aba "Alertas") — avisos por prioridade.
 *
 * O monitor do SAP e o agente do Forms só GRAVAM linhas. Este script decide
 * quem avisar, como e quando:
 *
 *   P1  e-mail individual na hora (+ Google Chat, se configurado), lembrete a
 *       cada 5 min, escala para o N2 com 15 min sem RECONHECIDO. 24 x 7.
 *   P2  um e-mail com a lista dos P2 novos a cada ciclo, lembrete aos 15 min,
 *       escala com 30 min. Horário comercial.
 *   P3  resumo de hora em hora, lembrete com 1 h, escala com 2 h. Horário comercial.
 *   P4  só no resumo diário das 8h, sem lembrete nem escalonamento.
 *
 * Casos novos são DISTRIBUÍDOS sozinhos para o analista N1 com menos casos em
 * andamento (empate: sorteio). Cada analista recebe só os seus casos.
 *
 * Agente Entrega Fácil: a ponte (ponte_agente.py) grava aqui os atendimentos do
 * agente. Os que ele mesmo concluiu chegam com responsavel "Agente IA" e status
 * RESOLVIDO/SEM_ACAO (não geram aviso nem e-mail ao cliente); os encaminhados a uma
 * equipe e os de revisão humana chegam ABERTO e seguem o fluxo normal abaixo.
 *
 * Cliente: só reclamações do Forms têm e-mail. Quando a linha FORM-xxxx fica
 * RESOLVIDO, AGUARDANDO_CLIENTE ou SEM_ACAO, o cliente recebe um e-mail-modelo.
 * O e-mail é lido na planilha de respostas do Forms e nunca é copiado para a aba
 * Alertas. Alertas só do SAP não têm contato: a ação é registrada na coluna causa.
 *
 * AGUARDANDO_CLIENTE: só para confirmar dados do cliente (reclamação do Forms em
 * revisão humana, RB-08). Em outra linha o status volta ao anterior. Pausa
 * lembretes e escalonamento.
 *
 * cupom_elegivel: ELEGIVEL / NAO_ELEGIVEL, preenchido quando vazio, pela regra do
 * runbook (só atraso confirmado com entrega realizada). O cupom em si é entregue
 * pelo agente de triagem; o e-mail do plantão não fala de cupom.
 *
 * MODO_DEMO encurta os prazos (DEMO_FATOR) e ignora o horário comercial, para a
 * apresentação: com 0.1, o P1 escala em 1,5 min e o P2 em 3 min.
 *
 * Os tempos de P2 a P4 contam só horário comercial (seg-sex, 8h-18h), como o SLA.
 * Linhas VINCULADO e SEM_ACAO não geram aviso: a reclamação vinculada vira um
 * aviso de ATUALIZAÇÃO para quem cuida do caso.
 *
 * As colunas são localizadas pelo NOME do cabeçalho (pode reordenar, não renomear).
 *
 * Instalação: cole este arquivo em Extensões > Apps Script, ajuste CONFIG,
 * confira o fuso do projeto (America/Sao_Paulo) e execute a função instalar()
 * uma vez. Ela cria os acionadores, a aba Equipe e a lista de status.
 */
const CONFIG = {
  ABA: "Alertas",
  ABA_EQUIPE: "Equipe",
  EMAIL_PLANTAO: "",                    // usado se a aba Equipe não tiver e-mail de N1 ("" = não envia)
  EMAIL_N2: "",                         // usado se a aba Equipe não tiver e-mail de N2 ("" = não envia)
  CHAT_WEBHOOK: "",                     // opcional: webhook de um espaço do Google Chat (P1)
  HORA_INICIO: 8,                       // horário comercial, segunda a sexta
  HORA_FIM: 18,
  ATRIBUIR_AUTOMATICO: true,            // distribui casos novos entre os N1 da aba Equipe
  AVISAR_CLIENTE: false,                // e-mail ao cliente nas reclamações do Forms (plano futuro)
  LER_FORMS: true,                      // cria um caso P4 para cada reclamação que o agente não resolveu
  FORMS_ESPERA_MIN: 10,                 // tempo que o agente tem para resolver (e apagar) antes de virar caso; na demo, 1
  FORMS_SHEET_ID: "1DTq-87TFJX7sNgoW-8BrlK8d17bk86ZHup1mUXndOFs", // planilha de respostas
  FORMS_ABA: "Respostas ao formulário 1",
  FORMS_ABA_CONTATOS: "Contatos",       // e-mails guardados no envio (contatos_forms.gs)
  FORMS_COL_EMAIL: "E-mail de contato",
  FORMS_COL_NOME: "Nome",
  REMETENTE: "PagFácil Atendimento",    // nome que o cliente vê
  RESPONSAVEL_AGENTE: "Agente IA",      // linhas fechadas pelo agente Entrega Fácil (ponte_agente.py)
  VIGIAR_MONITOR: true,                 // abre P1 se o monitor parar (aba Saude sem ciclo novo)
  ABA_SAUDE: "Saude",
  MONITOR_SEM_CICLO_MIN: 10,            // minutos sem ciclo para considerar o monitor parado
  MODO_DEMO: false,                     // true na apresentação
  DEMO_FATOR: 0.1,                      // prazos x 0.1 no modo demo
};

// aviso: individual (na hora) | lote (lista a cada ciclo) | hora (resumo por hora) | dia (resumo das 8h)
const REGRAS = {
  P1: { aviso: "individual", lembreteMin: 5,  repetirMin: 5, escalaMin: 15,  comercial: false },
  P2: { aviso: "lote",       lembreteMin: 15, repetirMin: 0, escalaMin: 30,  comercial: true },
  P3: { aviso: "hora",       lembreteMin: 60, repetirMin: 0, escalaMin: 120, comercial: true },
  P4: { aviso: "dia",        lembreteMin: 0,  repetirMin: 0, escalaMin: 0,   comercial: true },
};
const STATUS = ["ABERTO", "RECONHECIDO", "AGUARDANDO_CLIENTE", "RESOLVIDO", "VINCULADO", "SEM_ACAO"];
const EM_ANDAMENTO = ["ABERTO", "RECONHECIDO", "AGUARDANDO_CLIENTE"];

/** Regra da prioridade, com os prazos encurtados no modo demo. */
function regra_(gravidade) {
  const base = REGRAS[gravidade];
  if (!base || !CONFIG.MODO_DEMO) return base;
  const f = CONFIG.DEMO_FATOR;
  return {
    aviso: base.aviso === "individual" ? "individual" : "lote", // na demo tudo chega na hora
    lembreteMin: base.lembreteMin * f, repetirMin: base.repetirMin * f,
    escalaMin: base.escalaMin * f, comercial: false,
  };
}

// ------------------------------------------------------------------ utilitários
/** Mapa nome da coluna -> número da coluna (1, 2, 3...). */
function colunas_(aba) {
  const cabecalho = aba.getRange(1, 1, 1, aba.getLastColumn()).getValues()[0];
  const mapa = {};
  cabecalho.forEach((nome, i) => { if (nome) mapa[String(nome).trim()] = i + 1; });
  const obrigatorias = ["alerta_id", "regra", "id_pedido", "detalhe", "detectado_em", "gravidade",
                        "runbook", "status", "reconhecido_em", "resolvido_em", "avisado_em", "escalado_em"];
  const faltando = obrigatorias.filter((c) => !mapa[c]);
  if (faltando.length) throw new Error("Colunas ausentes na aba Alertas: " + faltando.join(", "));
  return mapa;
}

function valor_(linha, col, nome) {
  return col[nome] ? linha[col[nome] - 1] : "";
}

/** Quem vigia o vigia: P1 "monitor_parado" se a aba Saude ficar sem ciclo novo.
 *  Abre um caso (S-0001, S-0002...) quando passar de MONITOR_SEM_CICLO_MIN e fecha
 *  sozinho quando o monitor voltar. O aviso ao N1 sai pelo fluxo normal de P1. */
function vigiarMonitor_(planilha, aba, col, agora) {
  if (!CONFIG.VIGIAR_MONITOR) return;
  const saude = planilha.getSheetByName(CONFIG.ABA_SAUDE);
  if (!saude || saude.getLastRow() < 2) return; // monitor ainda nunca rodou
  const ultimo = paraData_(saude.getRange(saude.getLastRow(), 1).getValue());
  if (!ultimo) return;
  const semCiclo = (agora.getTime() - ultimo.getTime()) / 60000;
  const dados = aba.getDataRange().getValues();
  let aberto = -1, quantos = 0;
  for (let i = 1; i < dados.length; i++) {
    if (String(valor_(dados[i], col, "regra")) !== "monitor_parado") continue;
    quantos++;
    if (EM_ANDAMENTO.includes(String(valor_(dados[i], col, "status")).trim())) aberto = i + 1;
  }
  const fuso = Session.getScriptTimeZone();
  const quando = (d, f) => Utilities.formatDate(d, fuso, f || "yyyy-MM-dd HH:mm:ss");
  if (semCiclo > CONFIG.MONITOR_SEM_CICLO_MIN && aberto < 0) {
    const linha = new Array(aba.getLastColumn()).fill("");
    const por = { alerta_id: "S-" + String(quantos + 1).padStart(4, "0"), regra: "monitor_parado", id_pedido: "—",
      detalhe: `Monitor sem ciclo há ${Math.round(semCiclo)} min (último às ${quando(ultimo, "dd/MM HH:mm")})`,
      detectado_em: quando(agora), gravidade: "P1", runbook: "RB-10", status: "ABERTO", origem: "SAUDE",
      cupom_elegivel: "NAO_ELEGIVEL" };
    Object.keys(por).forEach((k) => { if (col[k]) linha[col[k] - 1] = por[k]; });
    aba.appendRow(linha);
  } else if (semCiclo <= CONFIG.MONITOR_SEM_CICLO_MIN / 2 && aberto > 0) {
    const set = (k, v) => { if (col[k]) aba.getRange(aberto, col[k]).setValue(v); };
    const atual = (k) => (col[k] ? aba.getRange(aberto, col[k]).getValue() : "");
    if (!atual("reconhecido_em")) set("reconhecido_em", quando(agora));
    if (!atual("responsavel")) set("responsavel", "Automático");
    set("resolvido_em", quando(agora));
    set("causa", `Monitor voltou a rodar às ${quando(ultimo, "HH:mm")}; caso fechado automaticamente`);
    set("status", "RESOLVIDO");
  }
}

/** Data da planilha (Date, "2026-11-18 14:56:45" ou "18/11/2026 14:56:45") -> Date. */
function paraData_(v) {
  if (v instanceof Date) return v;
  const s = String(v || "").trim();
  let m = s.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
  if (m) return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
  m = s.match(/^(\d{2})\/(\d{2})\/(\d{4}) (\d{2}):(\d{2})(?::(\d{2}))?/);
  if (m) return new Date(+m[3], +m[2] - 1, +m[1], +m[4], +m[5], +(m[6] || 0));
  return null;
}

function diaUtil_(d) {
  return d.getDay() >= 1 && d.getDay() <= 5;
}

function dentroDoHorario_(d) {
  return diaUtil_(d) && d.getHours() >= CONFIG.HORA_INICIO && d.getHours() < CONFIG.HORA_FIM;
}

/** Minutos entre inicio e agora; com comercial=true conta só seg-sex, das 8h às 18h. */
function minutosDecorridos_(inicio, agora, comercial) {
  if (!comercial) return (agora - inicio) / 60000;
  let total = 0;
  const dia = new Date(inicio.getFullYear(), inicio.getMonth(), inicio.getDate());
  for (let n = 0; n < 120 && dia <= agora; n++) {
    if (diaUtil_(dia)) {
      const abre = new Date(dia.getFullYear(), dia.getMonth(), dia.getDate(), CONFIG.HORA_INICIO);
      const fecha = new Date(dia.getFullYear(), dia.getMonth(), dia.getDate(), CONFIG.HORA_FIM);
      const de = Math.max(abre.getTime(), inicio.getTime());
      const ate = Math.min(fecha.getTime(), agora.getTime());
      if (ate > de) total += (ate - de) / 60000;
    }
    dia.setDate(dia.getDate() + 1);
  }
  return total;
}

function fmt_(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Aba Equipe: nome | email | nivel (N1/N2) | ativo (SIM/NÃO). */
function equipe_(planilha) {
  const eq = { n1: [], n2: [], porNome: {}, analistas: [] };
  const aba = planilha.getSheetByName(CONFIG.ABA_EQUIPE);
  if (aba && aba.getLastRow() > 1) {
    const linhas = aba.getRange(1, 1, aba.getLastRow(), 4).getValues();
    for (let i = 1; i < linhas.length; i++) {
      const [nome, email, nivel, ativo] = linhas[i].map((v) => String(v || "").trim());
      if ((!nome && !email) || ativo.toUpperCase().startsWith("N")) continue;
      // Todo N1 ativo entra na distribuição, com ou sem e-mail.
      // Quem não tem e-mail recebe casos, mas o aviso vai para o plantão (N1 com e-mail).
      if (nivel.toUpperCase() !== "N2") eq.analistas.push(nome || email);
      if (!email) continue;
      eq.porNome[nome.toLowerCase()] = email;
      eq.porNome[email.toLowerCase()] = email;
      if (nivel.toUpperCase() === "N2") eq.n2.push(email);
      else eq.n1.push(email);
    }
  }
  if (!eq.n1.length) eq.n1.push(CONFIG.EMAIL_PLANTAO);
  if (!eq.n2.length) eq.n2.push(CONFIG.EMAIL_N2);
  return eq;
}

function emailDe_(responsavel, eq) {
  const r = String(responsavel || "").trim();
  if (!r) return "";
  return eq.porNome[r.toLowerCase()] || (r.includes("@") ? r : "");
}

function enviar_(para, assunto, corpo, opcoes) {
  const lista = [...new Set([].concat(para).filter(Boolean))];
  if (!lista.length) return;
  try {
    MailApp.sendEmail(lista.join(","), assunto, corpo, opcoes || {});
  } catch (e) {
    console.error(`Falha ao enviar "${assunto}": ${e}`);
  }
}

function chat_(texto) {
  if (!CONFIG.CHAT_WEBHOOK) return;
  try {
    UrlFetchApp.fetch(CONFIG.CHAT_WEBHOOK, {
      method: "post", contentType: "application/json", payload: JSON.stringify({ text: texto }),
    });
  } catch (e) {
    console.error("Falha no Google Chat: " + e);
  }
}

function descrever_(item) {
  const d = item.d;
  const extra = item.min !== undefined ? ` · ${Math.round(item.min)} min sem resposta` : "";
  const dono = String(d("responsavel") || "").trim();
  return `• ${item.id} [${d("gravidade")}] ${d("regra")} · pedido ${d("id_pedido")}${extra}${dono ? " · " + dono : ""}\n` +
         `  ${d("detalhe")}\n  Runbook ${d("runbook")} · detectado ${d("detectado_em") instanceof Date ? fmt_(d("detectado_em")) : d("detectado_em")}`;
}

/** Lista curta para e-mail em lote: contagem por regra + os 5 primeiros. */
function listaCurta_(itens) {
  const porRegra = {};
  itens.forEach((x) => { const r = x.d("regra"); porRegra[r] = (porRegra[r] || 0) + 1; });
  const contagem = Object.keys(porRegra).map((r) => `${porRegra[r]} ${r}`).join(" · ");
  const mais = itens.length > 5 ? `\n\n… e mais ${itens.length - 5} na planilha.` : "";
  return `${contagem}\n\n${itens.slice(0, 5).map(descrever_).join("\n\n")}${mais}`;
}

/** Escolhe o N1 com menos casos em andamento; empate é sorteado. */
function escolherAnalista_(eq, carga) {
  if (!eq.analistas.length) return "";
  const menor = Math.min(...eq.analistas.map((n) => carga[n.toLowerCase()] || 0));
  const empatados = eq.analistas.filter((n) => (carga[n.toLowerCase()] || 0) === menor);
  return empatados[Math.floor(Math.random() * empatados.length)];
}

// ------------------------------------------------------------------ entrada do Forms
/** Reclamações que continuam no Forms depois de FORMS_ESPERA_MIN viram caso P4 (RB-08).
 *  O agente da Nathália apaga a resposta quando resolve; o que sobra é o que ele não
 *  resolveu e precisa de um analista. Só entram respostas enviadas depois que a leitura
 *  foi ligada (as de teste antigas ficam de fora). Na planilha de plantão entram só
 *  pedido, tipo e horário: nome, e-mail e relato do cliente ficam no Forms.
 *  Se o pedido já tem alerta do SAP, a reclamação fica VINCULADO a ele
 *  ("reclamação já vista pelo SAP" no Painel). */
function lerForms_(aba, col, agora) {
  if (!CONFIG.LER_FORMS || !CONFIG.FORMS_SHEET_ID) return;
  const props = PropertiesService.getScriptProperties();
  let desde = Number(props.getProperty("formsDesde") || 0);
  if (!desde) { desde = agora.getTime(); props.setProperty("formsDesde", String(desde)); return; }

  const forms = SpreadsheetApp.openById(CONFIG.FORMS_SHEET_ID).getSheetByName(CONFIG.FORMS_ABA);
  if (!forms || forms.getLastRow() < 2) return;
  const v = forms.getDataRange().getValues();
  const titulo = (t) => String(t).replace(/\s+/g, " ").trim().toLowerCase();
  const cab = v[0].map(titulo);
  const achar = (trecho) => cab.findIndex((c) => c.includes(trecho));
  const iPedido = achar("pedido"), iTipo = achar("tipo"), iNome = achar("nome"), iEmail = achar("e-mail"),
    iRelato = achar("descreva"), iCompra = achar("compra"), iCodigo = achar("transa"), iValor = achar("valor");
  const campo = (linha, idx) => {
    if (idx < 0) return "";
    const x = linha[idx];
    return x instanceof Date ? fmt(x, "dd/MM/yyyy HH:mm") : String(x == null ? "" : x).trim();
  };
  const fuso = Session.getScriptTimeZone();
  const fmt = (d, f) => Utilities.formatDate(d, fuso, f);

  const dados = aba.getDataRange().getValues();
  const existentes = new Set(), alertaDoPedido = {};
  for (let i = 1; i < dados.length; i++) {
    const id = String(valor_(dados[i], col, "alerta_id") || "").trim();
    existentes.add(id);
    const pedido = String(valor_(dados[i], col, "id_pedido") || "").trim();
    if (!/^A-/.test(id) || !pedido) continue;
    const andamento = EM_ANDAMENTO.includes(String(valor_(dados[i], col, "status")).trim());
    const atual = alertaDoPedido[pedido];
    if (!atual || (andamento && !atual.andamento) || (andamento === atual.andamento)) {
      alertaDoPedido[pedido] = { id, linha: i + 1, andamento,
        vinc: String(valor_(dados[i], col, "reclamacoes_vinculadas") || "") };
    }
  }

  const novos = [];
  for (let i = 1; i < v.length; i++) {
    const carimbo = paraData_(v[i][0]);
    if (!carimbo || carimbo.getTime() < desde) continue;
    if ((agora - carimbo) / 60000 < CONFIG.FORMS_ESPERA_MIN) continue; // o agente ainda pode resolver
    const id = "FORM-" + fmt(carimbo, "yyyyMMddHHmmss");
    if (existentes.has(id) || props.getProperty("form:" + id)) continue;
    const pedido = iPedido >= 0 ? String(v[i][iPedido] || "").replace(/\D/g, "") : "";
    const tipo = iTipo >= 0 ? String(v[i][iTipo] || "").trim() : "";
    const alvo = alertaDoPedido[pedido];
    const por = { alerta_id: id, regra: "reclamacao_forms", id_pedido: pedido || "—",
      detalhe: `Reclamação não resolvida pelo agente${tipo ? ": " + tipo : ""}` + (alvo ? `. Vinculada a ${alvo.id}, já detectado pelo monitor` : ""),
      detectado_em: fmt(agora, "yyyy-MM-dd HH:mm:ss"), gravidade: "P4", runbook: "RB-08",
      status: alvo ? "VINCULADO" : "ABERTO", origem: "FORMS", tipo_contestacao: tipo,
      id_reclamacao: id.slice(5), recebida_em: fmt(carimbo, "yyyy-MM-dd HH:mm:ss"),
      cupom_elegivel: "NAO_ELEGIVEL" };
    por.avisado_em = fmt(agora, "yyyy-MM-dd HH:mm:ss"); // o primeiro aviso sai agora (e-mail abaixo)
    const linha = new Array(aba.getLastColumn()).fill("");
    Object.keys(por).forEach((k) => { if (col[k]) linha[col[k] - 1] = por[k]; });
    aba.appendRow(linha);
    novos.push({ id, pedido: por.id_pedido, tipo, recebida: fmt(carimbo, "dd/MM/yyyy HH:mm"), alvo: alvo ? alvo.id : "",
      pedidoDigitado: campo(v[i], iPedido), nome: campo(v[i], iNome), email: campo(v[i], iEmail),
      relato: campo(v[i], iRelato), compra: campo(v[i], iCompra), codigo: campo(v[i], iCodigo), valor: campo(v[i], iValor) });
    if (alvo && col.reclamacoes_vinculadas) {
      alvo.vinc = alvo.vinc ? `${alvo.vinc}, ${id}` : id;
      aba.getRange(alvo.linha, col.reclamacoes_vinculadas).setValue(alvo.vinc);
    }
    existentes.add(id);
    props.setProperty("form:" + id, "1");
  }
  if (novos.length) emailForms_(novos, SpreadsheetApp.getActive().getUrl());
}

/** O que falta na reclamação para o N1 conseguir tratar (vira pedido de informação ao cliente). */
function faltasForms_(x) {
  const faltas = [];
  if (!/^\d{7}$/.test(String(x.pedidoDigitado || "").replace(/\D/g, ""))) faltas.push("número do pedido válido (7 dígitos)");
  if (!x.relato) faltas.push("descrição do que aconteceu");
  if (!x.compra) faltas.push("data da compra");
  if (!x.valor) faltas.push("valor do pedido");
  if (/cobran|pagamento|reembolso|estorno|valor/i.test(x.tipo) && !x.codigo) faltas.push("código da transação");
  return faltas;
}

/** Primeiro aviso das reclamações P4: e-mail em texto para o plantão (N1 com e-mail),
 *  com os dados do Forms. Só o e-mail leva nome, contato e relato; a planilha não. */
function emailForms_(itens, url) {
  const eq = equipe_(SpreadsheetApp.getActive());
  const lista = [...new Set(eq.n1.filter(Boolean))];
  if (!lista.length) return;
  const linha = "────────────────────────────────────────";
  const v = (t) => t || "não informado";
  const blocos = itens.map((x, n) => {
    const faltas = faltasForms_(x);
    const situacao = x.alvo
      ? `já vista pelo SAP. O monitor detectou este pedido no alerta ${x.alvo}. A reclamação foi juntada a ele.`
      : "nova para o N1. O agente não concluiu o atendimento.";
    const passo = faltas.length
      ? [`Pedir ao cliente: ${faltas.join("; ")}.`,
         "Mude o caso para AGUARDANDO_CLIENTE enquanto espera a resposta (o prazo pausa)."]
      : [x.alvo ? `Trate junto com ${x.alvo}, seguindo o runbook dele.` : "Confira o pedido e o nome na Gold e siga o RB do assunto.",
         "Mude para RECONHECIDO ao assumir."];
    return [
      `${n + 1}) ${x.id} · P4 · RB-08`,
      linha,
      `Situação: ${situacao}`,
      "",
      "Dados do Forms",
      `  Pedido: ${v(x.pedidoDigitado)}`,
      `  Tipo: ${v(x.tipo)}`,
      `  Recebida em: ${x.recebida}`,
      `  Cliente: ${v(x.nome)}`,
      `  Contato: ${v(x.email)}`,
      `  Data da compra: ${v(x.compra)}`,
      `  Valor: ${x.valor ? "R$ " + x.valor : "não informado"}`,
      `  Código transação: ${v(x.codigo)}`,
      `  Relato: ${x.relato ? "\"" + x.relato + "\"" : "não informado"}`,
      "",
      "Próximo passo",
      ...passo.map((t) => `  • ${t}`),
    ].join("\n");
  });
  const pedir = itens.filter((x) => faltasForms_(x).length).length;
  const corpo = [
    "Olá, plantão.",
    "",
    `Chegaram ${itens.length} reclamação(ões) pelo Forms que o agente não resolveu.` +
      (pedir ? ` ${pedir} precisa(m) de informação do cliente antes de seguir.` : ""),
    "Prazo P4: resposta em 1 dia útil e resolução em 5 dias úteis.",
    "",
    blocos.join("\n\n\n"),
    "",
    "",
    `Planilha de plantão: ${url}`,
    "",
    "NOC EntregaFácil · aviso automático",
  ].join("\n");
  try {
    MailApp.sendEmail(lista.join(","), `[P4] ${itens.length} reclamação(ões) do Forms para o plantão`, corpo,
      { name: "NOC EntregaFácil" });
  } catch (e) {
    console.error(`Falha ao enviar o aviso do Forms: ${e}`);
  }
}

/** Teste: manda o e-mail de exemplo para o plantão, sem mexer na planilha. Rode pelo editor. */
function testarEmailForms() {
  emailForms_([
    { id: "FORM-20261027101500", pedido: "5001849", pedidoDigitado: "5001849", tipo: "Atraso na entrega",
      recebida: "27/10/2026 10:15", alvo: "A-0042", nome: "Lucas Santos", email: "cliente.teste@email.com",
      relato: "Meu notebook chegou depois do prazo combinado.", compra: "21/10/2026 15:00", codigo: "", valor: "100" },
    { id: "FORM-20261027103200", pedido: "—", pedidoDigitado: "99999", tipo: "Cobrança indevida",
      recebida: "27/10/2026 10:32", alvo: "", nome: "Maria Silva", email: "maria.teste@email.com",
      relato: "", compra: "", codigo: "", valor: "" },
  ], SpreadsheetApp.getActive().getUrl());
}

// ------------------------------------------------------------------ cliente (Forms)
/** E-mail e nome do cliente da reclamação FORM-<carimbo>.
 *  O agente Entrega Fácil usa o carimbo de data/hora do Forms como ID
 *  (FORM-20261121153012 = resposta enviada em 21/11/2026 15:30:12) e apaga a
 *  resposta quando conclui o atendimento. Por isso o contato é procurado
 *  primeiro na aba "Contatos" (contatos_forms.gs, que guarda o e-mail no envio)
 *  e só depois na aba de respostas. Sem contato, o e-mail ao cliente não sai. */
function contatoCliente_(id, cache) {
  const m = String(id).match(/^FORM-(\d{14})(?:-\d+)?$/);
  if (!m || !CONFIG.FORMS_SHEET_ID) return null;
  if (!cache.contatos) {
    const planilha = SpreadsheetApp.openById(CONFIG.FORMS_SHEET_ID);
    const fuso = planilha.getSpreadsheetTimeZone();
    const chaveDe = (c) => c instanceof Date ? Utilities.formatDate(c, fuso, "yyyyMMddHHmmss") : String(c).replace(/\D/g, "");
    const titulo = (t) => String(t).replace(/\s+/g, " ").trim().toLowerCase();
    const porChave = {};
    // 1) aba de respostas (o que o agente ainda não apagou)
    const resp = planilha.getSheetByName(CONFIG.FORMS_ABA);
    if (resp) {
      const v = resp.getDataRange().getValues();
      const cab = v[0].map(titulo);
      const iEmail = cab.indexOf(titulo(CONFIG.FORMS_COL_EMAIL)), iNome = cab.indexOf(titulo(CONFIG.FORMS_COL_NOME));
      for (let i = 1; i < v.length; i++) {
        const email = iEmail >= 0 ? String(v[i][iEmail] || "").trim() : "";
        if (email) porChave[chaveDe(v[i][0])] = { email, nome: iNome >= 0 ? String(v[i][iNome] || "").trim().split(" ")[0] : "" };
      }
    }
    // 2) aba Contatos (guardada no envio; sobrevive à exclusão feita pelo agente)
    const cont = planilha.getSheetByName(CONFIG.FORMS_ABA_CONTATOS);
    if (cont) {
      const v = cont.getDataRange().getValues();
      for (let i = 1; i < v.length; i++) {
        const chave = String(v[i][0]).replace(/\D/g, "") || chaveDe(v[i][1]);
        if (chave && v[i][2]) porChave[chave] = { email: String(v[i][2]).trim(), nome: String(v[i][3] || "").trim() };
      }
    }
    cache.contatos = porChave;
  }
  return cache.contatos[m[1]] || null;
}

/** ELEGIVEL só para atraso confirmado com entrega realizada (política de compensação). */
function elegibilidade_(d) {
  const regra = String(d("regra") || "");
  const status = String(d("status") || "").trim();
  const m = String(d("detalhe") || "").match(/(\d+)\s*dia/);
  const dias = Number(d("dias_atraso")) || (m ? Number(m[1]) : 0);
  if (regra === "entrega_atrasada") return dias > 0 ? "ELEGIVEL" : "NAO_ELEGIVEL";
  if (regra === "reclamacao_atraso" && /^EF-/.test(String(d("cupom") || ""))) return "ELEGIVEL"; // cupom dado pelo agente
  if (regra === "reclamacao_atraso" && status !== "SEM_ACAO"
      && String(d("procede") || "").toLowerCase() === "sim" && dias > 0) return "ELEGIVEL";
  return "NAO_ELEGIVEL";
}

/** AGUARDANDO_CLIENTE só faz sentido quando é preciso confirmar dados (RB-08). */
function podeAguardarCliente_(aba, col, linha) {
  const id = String(aba.getRange(linha, col.alerta_id).getValue());
  const regra = String(aba.getRange(linha, col.regra).getValue());
  const rb = String(aba.getRange(linha, col.runbook).getValue());
  return /^FORM-/.test(id) && (rb === "RB-08" || /revisao_humana|nao_encontrado/.test(regra));
}

/** Causa como o cliente deve ler: sem prefixos internos do plantão. */
function causaParaCliente_(causa) {
  return String(causa || "")
    .replace(/^(Resolvida com o caso|Já resolvido no caso) [^:]+:\s*/, "")
    .replace(/^(Ação simulada|Incidente)(\s*\([^)]*\))?:\s*/i, "")
    .replace(/\.?\s*Cliente elegível a cupom[^.]*\.?/i, ".") // o cupom é assunto do agente
    .replace(/\.\.$/, ".")
    .trim();
}

/** Texto do e-mail ao cliente conforme o status da reclamação. */
function mensagemCliente_(status, d, contato) {
  const ola = `Olá${contato.nome ? ", " + contato.nome : ""}!`;
  const pedido = d("id_pedido");
  const assina = "\n\nAtenciosamente,\nEquipe de Atendimento PagFácil";
  if (status === "RESOLVIDO") {
    const causa = causaParaCliente_(d("causa"));
    return [`Pedido ${pedido}: sua reclamação foi resolvida`,
      `${ola}\n\nSua reclamação sobre o pedido ${pedido} foi tratada pela nossa equipe.` +
      (causa ? `\n\nO que foi feito: ${causa}` : "") + assina];
  }
  if (status === "AGUARDANDO_CLIENTE") {
    return [`Pedido ${pedido}: precisamos confirmar alguns dados`,
      `${ola}\n\nRecebemos sua reclamação, mas não conseguimos confirmar o pedido informado (${pedido}).` +
      `\n\nResponda este e-mail com o número do pedido e o nome usado na compra para seguirmos com o atendimento.` + assina];
  }
  if (status === "SEM_ACAO") {
    return [`Pedido ${pedido}: verificamos sua reclamação`,
      `${ola}\n\nVerificamos o pedido ${pedido} e ele está dentro do prazo previsto, sem problema identificado.` +
      `\n\nSe algo continuar errado, responda este e-mail que vamos olhar de novo.` + assina];
  }
  return null;
}

// ------------------------------------------------------------------ edição manual
/** Carimba os horários quando o N1 muda o status e fecha as reclamações vinculadas. */
function onEdit(e) {
  if (!e || !e.range) {
    // Rodado pelo botão Executar: não há edição para tratar. Esta função roda
    // sozinha quando alguém muda uma célula. Para configurar, execute "instalar".
    console.log("onEdit roda sozinho ao editar a planilha. Para configurar, execute a função instalar.");
    return;
  }
  const aba = e.range.getSheet();
  if (aba.getName() !== CONFIG.ABA) return;
  const col = colunas_(aba);
  const c0 = e.range.getColumn(), c1 = c0 + e.range.getNumColumns() - 1;
  if (col.status < c0 || col.status > c1) return;

  // Vale para uma célula ou para várias de uma vez (colar ou arrastar o status
  // num filtro: é assim que o N1 fecha em lote os pedidos de um surto P1).
  const r0 = Math.max(e.range.getRow(), 2), r1 = e.range.getRow() + e.range.getNumRows() - 1;
  if (r1 < r0) return;
  const novos = aba.getRange(r0, col.status, r1 - r0 + 1, 1).getValues();
  const umaCelula = e.range.getNumRows() === 1;
  const agora = new Date();
  for (let i = 0; i < novos.length; i++) {
    const linha = r0 + i, valor = String(novos[i][0]);
    const celula = aba.getRange(linha, col.status);
    if (valor === "AGUARDANDO_CLIENTE" && !podeAguardarCliente_(aba, col, linha)) {
      celula.setValue(umaCelula ? (e.oldValue || "ABERTO") : "ABERTO");
      celula.setNote("AGUARDANDO_CLIENTE é só para confirmar dados do cliente (reclamação do Forms em RB-08).");
      continue;
    }
    if (valor === "RECONHECIDO" || valor === "AGUARDANDO_CLIENTE" || valor === "RESOLVIDO") {
      if (!aba.getRange(linha, col.reconhecido_em).getValue()) aba.getRange(linha, col.reconhecido_em).setValue(agora);
    }
    if (valor === "RESOLVIDO") {
      aba.getRange(linha, col.resolvido_em).setValue(agora);
      fecharVinculadas_(aba, col, linha, agora);
    }
  }
}

/** Reclamações VINCULADO a um caso resolvido são resolvidas junto, com o mesmo horário. */
function fecharVinculadas_(aba, col, linha, quando) {
  if (!col.reclamacoes_vinculadas) return;
  const vinculadas = String(aba.getRange(linha, col.reclamacoes_vinculadas).getValue())
    .split(",").map((v) => v.trim()).filter(Boolean);
  if (!vinculadas.length) return;
  const causa = col.causa ? aba.getRange(linha, col.causa).getValue() : "";
  const alerta = aba.getRange(linha, col.alerta_id).getValue();
  const dados = aba.getDataRange().getValues();
  for (let i = 1; i < dados.length; i++) {
    const id = String(valor_(dados[i], col, "alerta_id")).trim();
    if (vinculadas.includes(id) && valor_(dados[i], col, "status") !== "RESOLVIDO") {
      aba.getRange(i + 1, col.status).setValue("RESOLVIDO");
      if (!valor_(dados[i], col, "reconhecido_em")) aba.getRange(i + 1, col.reconhecido_em).setValue(quando);
      aba.getRange(i + 1, col.resolvido_em).setValue(quando);
      if (col.causa && !valor_(dados[i], col, "causa")) {
        aba.getRange(i + 1, col.causa).setValue(`Resolvida com o caso ${alerta}${causa ? ": " + causa : ""}`);
      }
    }
  }
}

// ------------------------------------------------------------------ ciclo de 1 minuto
/** Acionador a cada minuto: avisos novos, lembretes, escalonamento, atribuição e vínculos. */
function ciclo() {
  const trava = LockService.getScriptLock();
  if (!trava.tryLock(20000)) return; // o ciclo anterior ainda está rodando
  try {
    ciclo_(new Date());
  } finally {
    trava.releaseLock();
  }
}

function ciclo_(agora) {
  const planilha = SpreadsheetApp.getActive();
  const aba = planilha.getSheetByName(CONFIG.ABA);
  const col = colunas_(aba);
  vigiarMonitor_(planilha, aba, col, agora);
  try {
    lerForms_(aba, col, agora);
  } catch (e) {
    console.error(`Falha ao ler o Forms: ${e}`); // não trava o resto do ciclo
  }
  const dados = aba.getDataRange().getValues();
  const eq = equipe_(planilha);
  const props = PropertiesService.getScriptProperties();
  const url = planilha.getUrl();
  const comercial = dentroDoHorario_(agora);
  const inicioOperacao = Number(props.getProperty("inicioOperacao") || 0);
  const ultimoP3 = Number(props.getProperty("ultimoResumoP3") || 0);
  const mandarP3 = comercial && agora.getTime() - ultimoP3 >= 60 * 60000;

  const novosLote = {}, novosHora = {}, escalar = [], lembretes = {}, atualizacoes = {};
  const gravar = []; // [linha, coluna, valor]
  const cache = {};

  // Carga atual de cada analista (casos em andamento), para distribuir os novos
  const carga = {};
  const donoDaVinculada = {}; // FORM-xxxx vinculada -> responsável do caso
  for (let i = 1; i < dados.length; i++) {
    const resp = String(valor_(dados[i], col, "responsavel") || "").trim();
    if (resp && EM_ANDAMENTO.includes(String(valor_(dados[i], col, "status")).trim())) {
      carga[resp.toLowerCase()] = (carga[resp.toLowerCase()] || 0) + 1;
    }
    String(valor_(dados[i], col, "reclamacoes_vinculadas") || "").split(",").map((v) => v.trim())
      .filter(Boolean).forEach((v) => { if (resp) donoDaVinculada[v] = resp; });
  }

  for (let i = 1; i < dados.length; i++) {
    const d = (nome) => valor_(dados[i], col, nome);
    const id = String(d("alerta_id")).trim();
    if (!id) continue;
    const status = String(d("status")).trim();
    const grav = String(d("gravidade")).trim();
    const regra = regra_(grav);
    const item = { id, d, linha: i + 1 };
    let responsavel = String(d("responsavel") || "").trim();

    // Distribuição: caso novo sem dono vai para o N1 com menos casos (aviso inicial já é dele)
    if (CONFIG.ATRIBUIR_AUTOMATICO && regra && status === "ABERTO" && !d("avisado_em") && !responsavel) {
      responsavel = escolherAnalista_(eq, carga);
      if (responsavel) {
        carga[responsavel.toLowerCase()] = (carga[responsavel.toLowerCase()] || 0) + 1;
        gravar.push([i + 1, col.responsavel, responsavel]);
        props.setProperty("resp:" + id, responsavel);
      }
    }
    const destino = emailDe_(responsavel, eq) || eq.n1;

    // Elegibilidade a cupom: preenche quando vazia (o analista pode corrigir à mão)
    if (col.cupom_elegivel && !String(d("cupom_elegivel") || "").trim()) {
      gravar.push([i + 1, col.cupom_elegivel, elegibilidade_(d)]);
    }

    // Cliente: só reclamações do Forms, um e-mail por status
    // Atendimento concluído pelo agente: o cliente já recebeu a resposta no chat
    const peloAgente = responsavel === CONFIG.RESPONSAVEL_AGENTE;
    if (CONFIG.AVISAR_CLIENTE && !peloAgente && /^FORM-/.test(id) && ["RESOLVIDO", "AGUARDANDO_CLIENTE", "SEM_ACAO"].includes(status)
        && !props.getProperty(`cli:${id}:${status}`)) {
      const contato = contatoCliente_(id, cache);
      const msg = contato && mensagemCliente_(status, d, contato);
      if (msg) {
        const quemResponde = emailDe_(donoDaVinculada[id], eq) || [].concat(destino)[0];
        enviar_(contato.email, msg[0], msg[1], { name: CONFIG.REMETENTE, replyTo: quemResponde });
        if (col.cliente_avisado_em) gravar.push([i + 1, col.cliente_avisado_em, agora]);
      }
      props.setProperty(`cli:${id}:${status}`, String(agora.getTime()));
    }

    // Atribuição: avisa quem foi colocado como responsável (uma vez por pessoa)
    if (responsavel && EM_ANDAMENTO.includes(status) && props.getProperty("resp:" + id) !== responsavel) {
      const email = emailDe_(responsavel, eq);
      if (email) {
        enviar_(email, `[${grav}] Você assumiu ${id} · ${d("regra")} · pedido ${d("id_pedido")}`,
          `${descrever_(item)}\n\nSiga o procedimento ${d("runbook")} do Runbook N1.\n${url}`);
      }
      props.setProperty("resp:" + id, responsavel);
    }

    // Vínculos: reclamação nova ligada ao caso vira aviso de atualização
    if (col.reclamacoes_vinculadas && EM_ANDAMENTO.includes(status)) {
      const atuais = String(d("reclamacoes_vinculadas") || "").split(",").map((v) => v.trim()).filter(Boolean);
      const antes = (props.getProperty("vinc:" + id) || "").split(",").filter(Boolean);
      const novas = atuais.filter((v) => !antes.includes(v));
      if (novas.length) {
        const chave = [].concat(destino).join(",");
        (atualizacoes[chave] = atualizacoes[chave] || []).push(`• ${id} [${grav}] pedido ${d("id_pedido")}: + ${novas.join(", ")}`);
        props.setProperty("vinc:" + id, atuais.join(","));
      }
    }

    if (status !== "ABERTO" || !regra) continue;

    // Aviso inicial
    if (!d("avisado_em")) {
      const chaveDestino = [].concat(destino).join(",");
      if (regra.aviso === "individual") {
        enviar_(eq.n1, `[P1] ${id} · ${d("regra")} · pedido ${d("id_pedido")}${responsavel ? " · responsável " + responsavel : ""}`,
          `${descrever_(item)}\n\nPrioridade 1: reconheça em até ${Math.round(regra.escalaMin)} min.\n${url}`);
        chat_(`🚨 P1 ${id} · ${d("regra")} · pedido ${d("id_pedido")}: ${d("detalhe")}`);
        gravar.push([i + 1, col.avisado_em, agora]);
      } else if (regra.aviso === "lote" && (comercial || CONFIG.MODO_DEMO)) {
        (novosLote[chaveDestino] = novosLote[chaveDestino] || []).push(item);
        gravar.push([i + 1, col.avisado_em, agora]);
      } else if (regra.aviso === "hora" && mandarP3) {
        (novosHora[chaveDestino] = novosHora[chaveDestino] || []).push(item);
        gravar.push([i + 1, col.avisado_em, agora]);
      }
      continue; // lembrete e escalonamento só depois do primeiro aviso
    }

    // Casos anteriores à instalação não geram lembrete nem escalonamento
    const inicio = paraData_(d("detectado_em"));
    if (!inicio || inicio.getTime() < inicioOperacao) continue;
    const min = minutosDecorridos_(inicio, agora, regra.comercial);

    if (regra.escalaMin && min >= regra.escalaMin) {
      if (!d("escalado_em")) {
        escalar.push({ ...item, min });
        gravar.push([i + 1, col.escalado_em, agora]);
      }
      continue;
    }
    if (regra.lembreteMin && min >= regra.lembreteMin && (!regra.comercial || comercial)) {
      const chaveLembrete = "lemb:" + id;
      const ultimo = Number(props.getProperty(chaveLembrete) || 0);
      const vence = regra.repetirMin ? agora.getTime() - ultimo >= regra.repetirMin * 60000 : !ultimo;
      if (vence) {
        const chave = [].concat(destino).join(",");
        (lembretes[chave] = lembretes[chave] || []).push({ ...item, min });
        props.setProperty(chaveLembrete, String(agora.getTime()));
      }
    }
  }

  // Envios em lote: um e-mail por tipo, nunca um por linha (exceto P1)
  Object.keys(novosLote).forEach((chave) => {
    const lista = novosLote[chave];
    const gravs = [...new Set(lista.map((x) => x.d("gravidade")))].join("/");
    enviar_(chave.split(","), `[${gravs}] ${lista.length} caso(s) novo(s) para você`,
      listaCurta_(lista) + `\n\nMarque RECONHECIDO ao assumir e siga o runbook indicado.\n${url}`);
  });
  Object.keys(novosHora).forEach((chave) => {
    const lista = novosHora[chave];
    enviar_(chave.split(","), `[P3] Resumo da hora: ${lista.length} caso(s) novo(s) para você`,
      listaCurta_(lista) + `\n\n${url}`);
  });
  if (mandarP3) props.setProperty("ultimoResumoP3", String(agora.getTime()));
  Object.keys(lembretes).forEach((chave) => {
    const lista = lembretes[chave];
    enviar_(chave.split(","), `[Lembrete] ${lista.length} caso(s) ainda sem resposta`,
      listaCurta_(lista) + `\n\nMarque RECONHECIDO ao assumir.\n${url}`);
  });
  if (escalar.length) {
    enviar_(eq.n2, `[ESCALADO] ${escalar.length} caso(s) sem resposta no prazo`,
      listaCurta_(escalar) + `\n\n${url}`);
    escalar.filter((x) => x.d("gravidade") === "P1")
      .forEach((x) => chat_(`⚠️ P1 ${x.id} escalado para o N2: ${Math.round(x.min)} min sem resposta`));
  }
  Object.keys(atualizacoes).forEach((chave) => {
    enviar_(chave.split(","), `[Atualização] Reclamação(ões) vinculada(s) a casos abertos`,
      atualizacoes[chave].join("\n") + `\n\n${url}`);
  });
  gravarEmLote_(aba, gravar);
}

/** Grava [linha, coluna, valor] coluna a coluna (uma leitura e uma escrita por coluna).
 *  Com milhares de linhas, setValue célula a célula estoura o limite de 6 min do Apps Script. */
function gravarEmLote_(aba, gravar) {
  const porColuna = {};
  gravar.forEach(([linha, coluna, valor]) => {
    if (coluna) (porColuna[coluna] = porColuna[coluna] || {})[linha] = valor;
  });
  Object.keys(porColuna).forEach((c) => {
    const coluna = Number(c), mudancas = porColuna[c];
    const linhas = Object.keys(mudancas).map(Number);
    if (linhas.length < 20) {
      linhas.forEach((l) => aba.getRange(l, coluna).setValue(mudancas[l]));
      return;
    }
    const primeira = Math.min.apply(null, linhas), ultima = Math.max.apply(null, linhas);
    const faixa = aba.getRange(primeira, coluna, ultima - primeira + 1, 1);
    const valores = faixa.getValues(); // leitura fresca: não apaga edição feita durante o ciclo
    linhas.forEach((l) => { valores[l - primeira][0] = mudancas[l]; });
    faixa.setValues(valores);
  });
}

// ------------------------------------------------------------------ resumo diário
/** Acionador diário às 8h: P4 novos + visão geral do que está aberto. */
function resumoDiario() {
  const agora = new Date();
  if (!diaUtil_(agora)) return;
  const planilha = SpreadsheetApp.getActive();
  const aba = planilha.getSheetByName(CONFIG.ABA);
  const col = colunas_(aba);
  const dados = aba.getDataRange().getValues();
  const eq = equipe_(planilha);
  const novosP4 = [];
  const abertos = { P1: 0, P2: 0, P3: 0, P4: 0 };
  let escalados = 0, aguardando = 0;
  for (let i = 1; i < dados.length; i++) {
    const d = (nome) => valor_(dados[i], col, nome);
    const id = String(d("alerta_id")).trim();
    const status = String(d("status")).trim();
    const grav = String(d("gravidade")).trim();
    if (!id || !EM_ANDAMENTO.includes(status)) continue;
    if (abertos[grav] !== undefined) abertos[grav]++;
    if (d("escalado_em")) escalados++;
    if (status === "AGUARDANDO_CLIENTE") aguardando++;
    if (grav === "P4" && status === "ABERTO" && !d("avisado_em") && !CONFIG.MODO_DEMO) {
      novosP4.push({ id, d });
      aba.getRange(i + 1, col.avisado_em).setValue(agora);
    }
  }
  const visao = `Em andamento: P1 ${abertos.P1} · P2 ${abertos.P2} · P3 ${abertos.P3} · P4 ${abertos.P4}` +
                ` · ${escalados} escalado(s) · ${aguardando} aguardando cliente`;
  const p4 = novosP4.length ? `\n\nP4 novos (${novosP4.length}):\n\n` + novosP4.map(descrever_).join("\n\n") : "";
  enviar_(eq.n1, `[Resumo do dia] ${visao}`, visao + p4 + `\n\n${planilha.getUrl()}`);
}

// ------------------------------------------------------------------ instalação
/**
 * Execute UMA vez (Executar > instalar). Cria os acionadores, a aba Equipe e a
 * lista suspensa de status. Casos já existentes não geram lembrete nem
 * escalonamento; os vínculos e responsáveis atuais não geram aviso.
 */
function instalar() {
  const planilha = SpreadsheetApp.getActive();
  const aba = planilha.getSheetByName(CONFIG.ABA);
  const col = colunas_(aba);

  const antigos = ["ciclo", "resumoDiario", "avisarNovosAlertas", "escalarP1"];
  ScriptApp.getProjectTriggers()
    .filter((t) => antigos.includes(t.getHandlerFunction()))
    .forEach((t) => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger("ciclo").timeBased().everyMinutes(1).create();
  ScriptApp.newTrigger("resumoDiario").timeBased().everyDays(1).atHour(CONFIG.HORA_INICIO).create();

  if (!planilha.getSheetByName(CONFIG.ABA_EQUIPE)) {
    const equipe = planilha.insertSheet(CONFIG.ABA_EQUIPE);
    equipe.getRange(1, 1, 2, 4).setValues([
      ["nome", "email", "nivel", "ativo"],
      ["Victor", CONFIG.EMAIL_PLANTAO, "N1", "SIM"],
    ]);
  }

  const regra = SpreadsheetApp.newDataValidation().requireValueInList(STATUS, true).build();
  aba.getRange(2, col.status, Math.max(aba.getMaxRows() - 1, 1), 1).setDataValidation(regra);

  const props = PropertiesService.getScriptProperties();
  props.deleteAllProperties(); // a planilha pode ter sido limpa e os IDs recomeçado
  props.setProperty("inicioOperacao", String(Date.now()));
  const dados = aba.getDataRange().getValues();
  for (let i = 1; i < dados.length; i++) {
    const id = String(valor_(dados[i], col, "alerta_id")).trim();
    if (!id) continue;
    const vinc = String(valor_(dados[i], col, "reclamacoes_vinculadas") || "").split(",").map((v) => v.trim()).filter(Boolean);
    if (vinc.length) props.setProperty("vinc:" + id, vinc.join(","));
    const resp = String(valor_(dados[i], col, "responsavel") || "").trim();
    if (resp) props.setProperty("resp:" + id, resp);
    const status = String(valor_(dados[i], col, "status")).trim();
    if (/^FORM-/.test(id)) props.setProperty(`cli:${id}:${status}`, "antes"); // não reenvia ao cliente
  }
  console.log("Instalado: acionadores ciclo (1 min) e resumoDiario (8h), aba Equipe e lista de status.");
}
