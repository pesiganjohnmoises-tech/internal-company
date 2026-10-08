"""Checks for the mapped (profile) import of indiav2.xlsx.

Run from the project folder:  python tests/test_mapped_import.py
The project (including directory.db) is copied to a temporary folder first; real data is never touched.
"""
import os, sys, re, json, shutil, sqlite3, tempfile, importlib
from pathlib import Path

SRC=Path(__file__).resolve().parent.parent
TMP=Path(tempfile.mkdtemp(prefix="mapped-import-test-"))
for name in ("app.py","utils","templates","static","data","directory.db"):
    p=SRC/name
    if p.is_dir(): shutil.copytree(p,TMP/name,ignore=shutil.ignore_patterns("__pycache__"))
    elif p.exists(): shutil.copy2(p,TMP/name)
sys.path.insert(0,str(TMP)); os.chdir(TMP)
from werkzeug.security import generate_password_hash
os.environ.update(APP_ENV="development",CLIENT_IP_HEADER="",TRUSTED_PROXY_CIDRS="",ADMIN_PASSWORD="Synthetic-Bootstrap-Password!")
A=importlib.import_module("app")
from utils import profile_import as P
from utils.importer import import_workbook
from utils.fields import build_company_detail
A.app.config.update(WTF_CSRF_ENABLED=False,TESTING=True,SECURITY_RATE_LIMITS={"login":10000,"directory":10000,"export":10000,"import":10000})

results=[]
def check(name,cond):
    results.append((name,bool(cond))); print(("PASS " if cond else "FAIL ")+name)
con=sqlite3.connect(A.DB); con.row_factory=sqlite3.Row
q=lambda sql,*a: con.execute(sql,a).fetchone()[0]
con.execute("UPDATE users SET password_hash=? WHERE username='admin'",(generate_password_hash("pw"),)); con.commit()
adm=A.app.test_client(); adm.post("/login",data={"username":"admin","password":"pw"})
india=q("SELECT id FROM countries WHERE name='India'")
def snapshot():
    """Every stored India company and contact, column by column."""
    return ([tuple(r) for r in con.execute("SELECT * FROM companies WHERE country_id=? ORDER BY id",(india,))],
            [tuple(r) for r in con.execute("SELECT ct.* FROM contacts ct JOIN companies co ON co.id=ct.company_id WHERE co.country_id=? ORDER BY ct.id",(india,))])
def counts(): return (q("SELECT COUNT(*) FROM companies"),q("SELECT COUNT(*) FROM contacts"),q("SELECT COUNT(*) FROM countries"))
def pending(): return sorted(p.name for p in (TMP/"imports").glob("*_indiav2.xlsx"))

# --- Profile --------------------------------------------------------------------------------------------
prof=P.load_profile("indiav2")
check("profile loads and maps onto the existing fields",prof["country"]=="India")
check("profile is found for indiav2.xlsx and browser copies, not for india.xlsx",
      P.profile_for("indiav2.xlsx") and P.profile_for("indiav2 (1).xlsx") and not P.profile_for("india.xlsx"))
bad=json.loads(json.dumps(prof)); city=next(c for c in bad["columns"] if c.get("field")=="city"); city.pop("field"); city["keep"]=True
try: P.check_profile(bad); ok=False
except ValueError: ok=True
check("profile check rejects a kept column the app would read as a database field",ok)
bad=json.loads(json.dumps(prof)); next(c for c in bad["columns"] if c.get("field")=="company_name")["required"]=False
try: P.check_profile(bad); ok=False
except ValueError: ok=True
check("profile check requires company_name",ok)
try: P.load_profile("../app"); ok=False
except KeyError: ok=True
check("profile names cannot reach outside import_profiles",ok)

# --- Analysis (read-only) --------------------------------------------------------------------------------
before=snapshot(); c0=counts()
a=P.analyze(TMP/"data/indiav2.xlsx",A.DB,prof)
s=a["summary"]
check("all 25 columns detected, none unknown",len(a["mapping"])==25 and not any(m.get("unknown") for m in a["mapping"]))
check("'State ' with a trailing space matches State",next(m for m in a["mapping"] if m["header"]=="State")["excel"]=="State")
check("Notes is flagged for review",next(m for m in a["mapping"] if m["header"]=="Notes")["review"])
check("rows without a company name are errors that block the import",s["total"]==154 and s["errors"]==10 and a["blocked"])
check("error rows are 146-155",[r["row"] for r in a["rows"] if r["errors"]]==list(range(146,156)))
a=P.analyze(TMP/"data/indiav2.xlsx",A.DB,prof,fill_down=True)
s=a["summary"]
check("fill-down assigns them to the company above",not a["blocked"] and {r["vals"]["company_name"] for r in a["rows"] if 146<=r["row"]<=155}=={"Freightwings Global Private Limited"})
check("existing contacts are detected as duplicates",s["duplicates"]==39 and s["existing_companies"]==9)
check("summary adds up",s["new_contacts"]+s["skipped"]==s["total"] and s["new_companies"]==56)
check("unreadable expiry dates are warnings, not errors",any("not a readable date" in w for r in a["rows"] for w in r["warnings"]))
check("company spelled differently is flagged",any("spelled differently" in w for r in a["rows"] for w in r["warnings"]))
check("analysis writes nothing",snapshot()==before and counts()==c0)
xl=P.analyze(TMP/"data/indiav2.xlsx",A.DB,prof,fill_down=True,mode="skip_existing")["summary"]
check("skip-existing mode leaves existing companies out",xl["new_contacts"]==111 and xl["skipped"]==43)

# --- Pages -----------------------------------------------------------------------------------------------
h=adm.get("/admin/imports").data.decode()
check("imports page offers the mapped review for data/indiav2.xlsx",'value="indiav2.xlsx">Review data/indiav2.xlsx' in h)
check("standard data/ row warns that it replaces a country","Has its own mapped import below" in h)
h=adm.post("/admin/imports/preview",data={"filename":"indiav2.xlsx"},follow_redirects=True).data.decode()
check("standard preview of indiav2.xlsx is refused and points to the mapped import","has its own mapped import" in h and "<h1>Replace " not in h)
r=adm.post("/admin/imports/mapped/indiav2/upload",data={"filename":"indiav2.xlsx"})
check("review starts from a private copy",r.status_code==302 and len(pending())==1)
pend=pending()[0]
h=adm.get(f"/admin/imports/mapped/indiav2/review?pending={pend}").data.decode()
check("review page renders, import disabled while errors remain",r.status_code==302 and "Cannot import" not in h and re.search(r'<button class="primary" disabled>Import',h))
h=adm.get(f"/admin/imports/mapped/indiav2/review?pending={pend}&fill_down=1").data.decode()
m=re.search(r'name="plan" value="([0-9a-f]+)"',h); plan=m.group(1) if m else ""
check("review with fill-down enables the import","Import 115 rows into India" in h and plan)
check("review shows mapping and rows","Column mapping" in h and "Freightwings Global Private Limited" in h)
check("review rejects a bad pending name",adm.get("/admin/imports/mapped/indiav2/review?pending=../directory.db").status_code==400)
check("unknown profile is 404",adm.get(f"/admin/imports/mapped/nope/review?pending={pend}").status_code==404)
check("non-admin cannot open the review",A.app.test_client().get(f"/admin/imports/mapped/indiav2/review?pending={pend}").status_code in (302,403))

# --- Safety: stale plan, blocked plan, rollback ---------------------------------------------------------
adm.post("/admin/imports/mapped/indiav2/confirm",data={"pending":pend,"plan":"0"*20,"fill_down":"1"})
check("stale preview hash imports nothing",snapshot()==before and counts()==c0 and pending()==[pend])
adm.post("/admin/imports/mapped/indiav2/confirm",data={"pending":pend,"plan":P.analyze(TMP/"imports"/pend,A.DB,prof)["hash"]})
check("blocked plan imports nothing",snapshot()==before and counts()==c0)
con.execute("CREATE TRIGGER boom AFTER INSERT ON contacts WHEN (SELECT COUNT(*) FROM contacts WHERE source_file LIKE '%indiav2.xlsx')>50 BEGIN SELECT RAISE(ABORT,'simulated failure'); END"); con.commit()
try: P.apply(TMP/"imports"/pend,A.DB,prof,plan,pend,fill_down=True); ok=False
except sqlite3.DatabaseError: ok=True
check("a failure halfway rolls the whole import back",ok and snapshot()==before and counts()==c0)
con.execute("DROP TRIGGER boom"); con.commit()

# --- Import ----------------------------------------------------------------------------------------------
history=q("SELECT COUNT(*) FROM imports")
h=adm.post("/admin/imports/mapped/indiav2/confirm",data={"pending":pend,"plan":plan,"fill_down":"1","mode":"add"}).data.decode()
after=snapshot()
check("summary page shows the counts","imported into India" in h and "Backup saved as" in h)
check("56 companies and 115 contacts added",counts()==(c0[0]+56,c0[1]+115,c0[2]))
check("no existing company or contact changed",after[0][:len(before[0])]==before[0] and after[1][:len(before[1])]==before[1])
check("pending copy removed after import",pending()==[])
row=con.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 1").fetchone()
check("import logged in history",q("SELECT COUNT(*) FROM imports")==history+1 and row["status"]=="COMPLETED" and row["records_processed"]==154 and row["records_imported"]==115 and row["duplicates_found"]==39)
check("import logged in app.log","mapped import profile=indiav2" in (TMP/"app.log").read_text(encoding="utf-8",errors="ignore"))
new=con.execute("SELECT * FROM companies WHERE company_name='Ace Forwarders Pvt. Ltd.' AND country_id=?",(india,)).fetchone()
fw=con.execute("SELECT id FROM companies WHERE company_name='Freightwings Global Private Limited' AND country_id=?",(india,)).fetchone()
check("filled-down rows went to the existing company",q("SELECT COUNT(*) FROM contacts WHERE company_id=? AND source_file LIKE '%indiav2.xlsx'",fw["id"])>=1)
aero=con.execute("SELECT * FROM companies WHERE company_name LIKE 'Aerotrans%' AND country_id=?",(india,)).fetchone()
src=json.loads(aero["source_data"])
check("new company stored like the standard import",aero["city"] and aero["network"] and "State" in src and "KG Sales" in src and "Notes" in src)
h=adm.get(f"/company/{aero['id']}").data.decode()
check("company page renders the imported record",">Notes<" in h and 'class="field-label">Kargosmart Sales<' in h)
d=build_company_detail(con.execute("SELECT co.*,cn.name country FROM companies co JOIN countries cn ON cn.id=co.country_id WHERE co.id=?",(aero["id"],)).fetchone(),con.execute("SELECT * FROM contacts WHERE company_id=?",(aero["id"],)).fetchall(),lambda k: k not in A.SALES_FIELDS)
check("sales columns still follow sales permissions",not any(f["key"] in A.SALES_FIELDS for s_ in d["sections"] for f in s_["fields"]))
check("search finds an imported company",b"Aerotrans" in adm.get("/search?q=Aerotrans").data)
# Source of Agent and Notes: contact columns after Street address; Notes per row, not on the company sheets.
noted=[r for r in con.execute("SELECT * FROM contacts WHERE source_file LIKE '%indiav2.xlsx'") if (json.loads(r["source_data"]).get("Notes") or "").strip()]
nc=noted[0]; note=json.loads(nc["source_data"])["Notes"].strip()
d=build_company_detail(con.execute("SELECT co.*,cn.name country FROM companies co JOIN countries cn ON cn.id=co.country_id WHERE co.id=?",(nc["company_id"],)).fetchone(),con.execute("SELECT * FROM contacts WHERE company_id=?",(nc["company_id"],)).fetchall(),lambda k: True)
cols=[k for k,l in d["contact_columns"]]
check("contacts: Source of Agent then Notes right after Street address",cols[cols.index("address")+1:cols.index("address")+3]==["x_sourceofagent","x_notes"])
check("contacts: each row shows its own Notes",next(c for c in d["contacts"] if c["id"]==nc["id"])["by_key"]["x_notes"]["value"].get("text")==note)
check("contacts: Notes not repeated on the company sheets",not any(f["key"]=="x_notes" for s_ in d["sections"] for f in s_["fields"]))
check("contacts: Notes of one row not copied to rows that leave it blank",all(c["by_key"]["x_notes"]["value"].get("empty") for c in d["contacts"] if not (json.loads(con.execute("SELECT source_data FROM contacts WHERE id=?",(c["id"],)).fetchone()[0]).get("Notes") or "").strip()))
h=adm.get(f"/company/{nc['company_id']}").data.decode()
check("company page: Notes column header and value, admin column renamed",'class="field-label">Notes<' in h and note in h and '<th scope="col" data-cell>Admin</th>' in h)
again=P.analyze(TMP/"data/indiav2.xlsx",A.DB,prof,fill_down=True)["summary"]
check("importing the same file again adds nothing",again["new_contacts"]==0 and again["new_companies"]==0 and again["duplicates"]==154)

# --- Update mode -----------------------------------------------------------------------------------------
ace=con.execute("SELECT * FROM companies WHERE company_name='Ace Forwarders Pvt. Ltd.' AND country_id=?",(india,)).fetchone()
con.execute("UPDATE companies SET network=NULL,state='Keep me' WHERE id=?",(ace["id"],)); con.commit()
adm.post("/admin/imports/mapped/indiav2/upload",data={"filename":"indiav2.xlsx"}); pend=pending()[0]
up=P.analyze(TMP/"imports"/pend,A.DB,prof,fill_down=True,mode="update")
c1=counts()
done=P.apply(TMP/"imports"/pend,A.DB,prof,up["hash"],pend,fill_down=True,mode="update")
ace2=con.execute("SELECT * FROM companies WHERE id=?",(ace["id"],)).fetchone()
check("update mode fills and overwrites from the file",ace2["network"]=="WCA" and ace2["state"]=="Maharashtra")
check("update mode adds and deletes nothing",counts()==c1 and done["result"]["new_contacts"]==0)
check("update keeps stored columns the file leaves blank",json.loads(ace2["source_data"]).get("Company Name Entity")=="Ace Forwarders Pvt. Ltd.")

# --- The standard import is unchanged ----------------------------------------------------------------------
r=import_workbook(TMP/"data/india.xlsx",A.DB,"India",replace_country=True,dry_run=True)
check("standard India import still previews as before",r["country"]=="India" and r["imported"]>0)
check("standard import still derives the country from the file name",A.detect_country("singapore.xlsx")=="Singapore")

passed=sum(ok for _,ok in results)
print(f"\n{passed}/{len(results)} passed  (temp copy: {TMP})")
sys.exit(0 if passed==len(results) else 1)
