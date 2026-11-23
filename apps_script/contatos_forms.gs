/**
 * Guarda o e-mail do cliente numa aba "Contatos" da planilha de respostas,
 * para o plantão conseguir responder mesmo depois que o agente apagar a resposta.
 *
 * Por quê: o agente Entrega Fácil apaga a linha do Forms ao concluir o
 * atendimento. Sem o e-mail, o analista não tem como dar retorno nos casos
 * encaminhados (Logística, Financeiro, Trocas). Esta aba guarda só o mínimo:
 * a chave do caso (carimbo = ID FORM-<carimbo> do agente), o e-mail e o
 * primeiro nome. Nada de relato, pedido ou valor.
 *
 * Onde colar: no FORMULÁRIO, menu ⋮ > Editor de scripts, como um arquivo novo
 * ao lado do script de confirmação da Nathália (os nomes não colidem).
 * Depois, rode `instalarContatos` uma vez e autorize.
 */
const CONTATOS = {
  ABA: "Contatos",
  DIAS_GUARDAR: 90, // depois disso o contato é apagado (LGPD: só o necessário)
};

function instalarContatos() {
  const form = FormApp.getActiveForm();
  ScriptApp.getProjectTriggers()
    .filter((t) => ["guardarContato", "apagarContatosAntigos"].includes(t.getHandlerFunction()))
    .forEach((t) => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger("guardarContato").forForm(form).onFormSubmit().create();
  ScriptApp.newTrigger("apagarContatosAntigos").timeBased().everyDays(1).atHour(3).create();
  abaContatos_();
  copiarContatosExistentes();
  console.log("Contatos instalado na planilha " + planilha_().getName());
}

/** Acionador "Ao enviar formulário". */
function guardarContato(e) {
  const respostas = e.response.getItemResponses().map((item) => ({
    titulo: normalizar_(item.getItem().getTitle()),
    valor: String(item.getResponse() || "").trim(),
  }));
  const achar = (inicio) => (respostas.find((r) => r.titulo.indexOf(inicio) === 0) || {}).valor || "";
  const email = achar("e mail") || achar("email") || e.response.getRespondentEmail() || "";
  if (!/^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$/.test(email)) return;
  const nome = achar("nome").split(/\s+/)[0];
  registrar_(e.response.getTimestamp(), email, nome);
}

/** Copia para a aba Contatos as respostas que já estão na planilha. */
function copiarContatosExistentes() {
  const sh = planilha_();
  const respostas = sh.getSheets().filter((a) => a.getName() !== CONTATOS.ABA && /respostas/i.test(a.getName()));
  respostas.forEach((aba) => {
    const v = aba.getDataRange().getValues();
    if (v.length < 2) return;
    const cab = v[0].map(normalizar_);
    const iEmail = cab.findIndex((c) => c.indexOf("e mail") === 0 || c.indexOf("email") === 0);
    const iNome = cab.findIndex((c) => c.indexOf("nome") === 0);
    for (let i = 1; i < v.length; i++) {
      if (v[i][0] instanceof Date && iEmail >= 0 && v[i][iEmail]) {
        registrar_(v[i][0], String(v[i][iEmail]).trim(), iNome >= 0 ? String(v[i][iNome]).split(/\s+/)[0] : "");
      }
    }
  });
}

function apagarContatosAntigos() {
  const aba = abaContatos_();
  const v = aba.getDataRange().getValues();
  const limite = Date.now() - CONTATOS.DIAS_GUARDAR * 864e5;
  for (let i = v.length - 1; i >= 1; i--) {
    if (v[i][1] instanceof Date && v[i][1].getTime() < limite) aba.deleteRow(i + 1);
  }
}

// ---------------------------------------------------------------- internos
function planilha_() {
  return SpreadsheetApp.openById(FormApp.getActiveForm().getDestinationId());
}

function abaContatos_() {
  const sh = planilha_();
  let aba = sh.getSheetByName(CONTATOS.ABA);
  if (!aba) {
    // Sempre a ÚLTIMA aba: o agente lê a primeira quando FORMS_SHEET_ABA está vazio
    aba = sh.insertSheet(CONTATOS.ABA, sh.getSheets().length);
    aba.appendRow(["chave", "carimbo", "email", "primeiro_nome"]);
    aba.getRange("A:A").setNumberFormat("@");
    aba.setFrozenRows(1);
  }
  return aba;
}

function registrar_(carimbo, email, nome) {
  const sh = planilha_();
  // Mesmo fuso da planilha: a chave tem de bater com o ID FORM-<carimbo> do agente
  const chave = Utilities.formatDate(carimbo, sh.getSpreadsheetTimeZone(), "yyyyMMddHHmmss");
  const aba = abaContatos_();
  const chaves = aba.getRange(1, 1, Math.max(aba.getLastRow(), 1), 1).getValues().map((l) => String(l[0]));
  if (chaves.includes(chave)) return;
  aba.appendRow([chave, carimbo, email, nome]);
}

function normalizar_(texto) {
  return String(texto || "").normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}
