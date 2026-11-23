"""Diagnóstico da configuração: Databricks, planilha de plantão e Forms. Não grava nada e não mostra senhas."""
import os, sys, traceback
from pathlib import Path
from dotenv import load_dotenv

PASTA = Path(__file__).resolve().parent
load_dotenv(PASTA / ".env")
ok = True

def passo(nome, func):
    global ok
    print(f"\n== {nome}")
    try:
        func()
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"FALHOU: {type(e).__name__}: {str(e)[:400]}")

def databricks():
    from databricks import sql
    host = os.getenv("DATABRICKS_SERVER_HOSTNAME", "").strip()
    path = os.getenv("DATABRICKS_HTTP_PATH", "").strip()
    tok = os.getenv("DATABRICKS_TOKEN", "").strip()
    print(f"host={host} path={path} token={len(tok)} caracteres")
    with sql.connect(server_hostname=host, http_path=path, access_token=tok) as c, c.cursor() as cur:
        cur.execute("SHOW CATALOGS"); cats = [r[0] for r in cur.fetchall()]
        print("catálogos:", cats)
        for cat in cats:
            if cat in ("system", "samples", "hive_metastore"):
                continue
            try:
                cur.execute(f"SHOW SCHEMAS IN `{cat}`"); schemas = [r[0] for r in cur.fetchall()]
            except Exception as e:  # noqa: BLE001
                print(f"  {cat}: sem acesso ({type(e).__name__})"); continue
            for sch in schemas:
                if sch == "information_schema":
                    continue
                try:
                    cur.execute(f"SHOW TABLES IN `{cat}`.`{sch}`"); tabs = [r[1] for r in cur.fetchall()]
                except Exception as e:  # noqa: BLE001
                    print(f"  {cat}.{sch}: sem acesso ({type(e).__name__})"); continue
                print(f"  {cat}.{sch}: {', '.join(tabs[:25])}{' ...' if len(tabs) > 25 else ''}")

def _cliente():
    import gspread
    cred = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    cred = cred if os.path.isabs(cred) else str(PASTA / cred)
    return gspread.service_account(filename=cred)

def plantao():
    sh = _cliente().open_by_key(os.getenv("PLANTAO_SHEET_ID", "").strip())
    print("planilha:", sh.title, "| abas:", [w.title for w in sh.worksheets()])
    print("cabeçalho Alertas:", len(sh.worksheet("Alertas").row_values(1)), "colunas")

def forms():
    sh = _cliente().open_by_key(os.getenv("FORMS_SHEET_ID", "").strip())
    abas = [w.title for w in sh.worksheets()]
    print("planilha:", sh.title, "| abas:", abas)
    aba = os.getenv("FORMS_SHEET_ABA", "").strip()
    print("FORMS_SHEET_ABA:", repr(aba), "-> existe" if aba in abas else "-> NÃO existe (a ponte usaria a primeira aba)")

print("Python", sys.version.split()[0])
passo("Databricks", databricks)
passo("Planilha de plantão (service account)", plantao)
passo("Planilha do Forms (service account)", forms)
print("\nRESULTADO:", "tudo ok" if ok else "há falhas acima")
