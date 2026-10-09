"""Release 1 regression checks using only code copies and synthetic data.

Run: python tests/test_security_release1.py
No live database, credentials, workbooks, logs, or deployment route are accessed.
"""
import csv
import importlib
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
from unittest.mock import patch

from openpyxl import Workbook

SRC=Path(__file__).resolve().parent.parent
TMP=Path(tempfile.mkdtemp(prefix="directory-security-test-"))
for name in ("app.py","utils","templates","static"):
    source=SRC/name
    if source.is_dir(): shutil.copytree(source,TMP/name,ignore=shutil.ignore_patterns("__pycache__"))
    else: shutil.copy2(source,TMP/name)
# Explicit synthetic configuration; never inherit deployment credentials.
os.environ.update(SECRET_KEY="synthetic-security-test-key",ADMIN_PASSWORD="Synthetic-Admin-Password!",
                  DEPLOY_SECRET="",COOKIE_SECURE="0",APP_ENV="development",CLIENT_IP_HEADER="",TRUSTED_PROXY_CIDRS="",
                  MFA_ENABLED="0",MFA_ENROLLMENT_ENABLED="0",MFA_ENCRYPTION_KEY="",MFA_ENCRYPTION_KEY_FILE="")
os.chdir(TMP)
sys.path.insert(0,str(TMP))
A=importlib.import_module("app")
A.app.config.update(TESTING=True,WTF_CSRF_ENABLED=True)
checks=0

def check(name,condition):
    global checks
    assert condition,name
    checks+=1
    print("PASS "+name)

def token(client,path="/account/password"):
    html=client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"',html).group(1)

def login(username,password):
    client=A.app.test_client()
    response=client.post("/login",data={"username":username,"password":password,"csrf_token":token(client,"/login")})
    check("valid CSRF login "+username,response.status_code==302)
    return client

def snapshot():
    # Request counters and session timestamps are expected security bookkeeping, not business writes.
    with sqlite3.connect(A.DB) as con: return [line for line in con.iterdump() if not line.startswith('INSERT INTO "security_')]

def workbook(path,headers=("Company","Name"),row=("Synthetic Company","Synthetic Contact")):
    wb=Workbook(); ws=wb.active; ws.append(headers); ws.append(row); wb.save(path); wb.close()

with A.db() as con:
    con.execute("INSERT INTO users(username,password_hash,role,status) VALUES(?,?,?,?)",
                ("kharla",A.generate_password_hash("Synthetic-User-Password!"),"USER","ACTIVE"))
admin=login("admin","Synthetic-Admin-Password!")
user=login("kharla","Synthetic-User-Password!")
profile={"name":"security_test","title":"Synthetic mapping","country":"Synthetic Country",
         "file_pattern":r"mapped\.xlsx$", "columns":[{"header":"Company","field":"company_name","required":True},
                                             {"header":"Name","field":"name"}]}
(TMP/"utils/import_profiles/security_test.json").write_text(json.dumps(profile),encoding="utf-8")
with A.db() as con:
    uid=con.execute("SELECT id FROM users WHERE username='kharla'").fetchone()[0]

# CSRF must stop handlers before any database or pending-file change.
pending=A.IMPORTS/"0123456789ab_synthetic.xlsx"
workbook(pending)
routes=[("/login",{"username":"admin","password":"Synthetic-Admin-Password!"}),
        ("/logout",{}),
        ("/account/password",{"current_password":"Synthetic-Admin-Password!","new_password":"Synthetic-New-Password!","confirm_password":"Synthetic-New-Password!"}),
        ("/admin/users/create",{"username":"newuser","password":"Synthetic-New-Password!"}),
        (f"/admin/users/{uid}",{"role":"FULL_ACCESS","status":"ACTIVE"}),
        (f"/admin/users/{uid}/delete",{}),
        ("/admin/imports",{}),
        ("/admin/imports/preview",{"filename":"synthetic.xlsx"}),
        ("/admin/imports/seed",{"filename":"synthetic.xlsx"}),
        ("/admin/imports/upload/confirm",{"pending":pending.name}),
        ("/admin/imports/discard",{"pending":pending.name}),
        ("/admin/imports/mapped/security_test/upload",{}),
        ("/admin/imports/mapped/security_test/confirm",{"pending":pending.name})]
for mode in ("missing","invalid","expired"):
    csrf_token=token(admin)
    before=snapshot(); contents=pending.read_bytes()
    original=A.app.config.get("WTF_CSRF_TIME_LIMIT",3600)
    if mode=="expired": A.app.config["WTF_CSRF_TIME_LIMIT"]=-1
    try:
        for path,data in routes:
            data=dict(data)
            if mode!="missing": data["csrf_token"]="invalid" if mode=="invalid" else csrf_token
            response=admin.post(path,data=data)
            check(f"{mode} CSRF rejects {path}",response.status_code==400)
            check("CSRF rejection is no-store",response.headers.get("Cache-Control")=="no-store")
        check(mode+" CSRF changes no data or files",snapshot()==before and pending.read_bytes()==contents)
    finally: A.app.config["WTF_CSRF_TIME_LIMIT"]=original

# Real valid forms, imports and permission checks with CSRF left enabled.
response=admin.post("/admin/users/create",data={"username":"newuser","first_name":"Test","last_name":"User",
                    "password":"Synthetic-New-Password!","csrf_token":token(admin)})
with A.db() as con: new=con.execute("SELECT id FROM users WHERE username='newuser'").fetchone()
check("valid CSRF user creation",response.status_code==302 and new is not None)
response=admin.post(f"/admin/users/{new['id']}",data={"role":"USER","status":"ACTIVE","first_name":"Changed",
                    "last_name":"User","csrf_token":token(admin)})
with A.db() as con: name=con.execute("SELECT first_name FROM users WHERE id=?",(new["id"],)).fetchone()[0]
check("valid CSRF user edit",response.status_code==302 and name=="Changed")
check("restricted user cannot access admin",user.get("/admin/users").status_code==403)
check("restricted user cannot export",user.get("/admin/export/companies.csv").status_code==403)
for role in ("USER","FULL_ACCESS"):
    with A.db() as con: con.execute("UPDATE users SET role=? WHERE id=?",(role,uid))
    check(role+" menu hides password change",b'href="/account/password"' not in user.get("/search").data)
    check(role+" cannot open password page",user.get("/account/password").status_code==403)
    before=snapshot()
    response=user.post("/account/password",data={"current_password":"Synthetic-User-Password!",
                       "new_password":"Synthetic-Forbidden-Password!","confirm_password":"Synthetic-Forbidden-Password!",
                       "csrf_token":token(user,"/search")})
    check(role+" cannot change own password with valid CSRF",response.status_code==403 and snapshot()==before)
    response=user.post(f"/admin/users/{uid}",data={"role":"ADMIN","status":"ACTIVE","password":"Synthetic-Forbidden-Password!",
                       "csrf_token":token(user,"/search")})
    check(role+" cannot reset password through admin route",response.status_code==403 and snapshot()==before)
with A.db() as con: con.execute("UPDATE users SET role='USER' WHERE id=?",(uid,))
check("admin menu keeps password change",b'href="/account/password"' in admin.get("/search").data)
response=admin.post(f"/admin/users/{uid}",data={"role":"USER","status":"ACTIVE","password":"Synthetic-Reset-Password!",
                    "csrf_token":token(admin)})
check("admin resets user password",response.status_code==302)
check("admin reset invalidates existing user session",user.get("/search").status_code==302)
user=login("kharla","Synthetic-Reset-Password!")
old=A.app.test_client()
old.post("/login",data={"username":"kharla","password":"Synthetic-User-Password!","csrf_token":token(old,"/login")})
check("old user password no longer signs in",old.get("/search").status_code==302)

response=admin.post("/admin/imports",data={"file":(io.BytesIO(pending.read_bytes()),"synthetic.xlsx"),
                    "country":"Synthetic Country","csrf_token":token(admin)},content_type="multipart/form-data")
check("valid standard import preview",response.status_code==200 and b"Replace Synthetic Country data" in response.data)
preview_name=re.search(rb'name="pending" value="([^"]+)"',response.data).group(1).decode()
response=admin.post("/admin/imports/upload/confirm",data={"pending":preview_name,"country":"Synthetic Country","csrf_token":token(admin)})
with A.db() as con: company=con.execute("SELECT id FROM companies WHERE company_name='Synthetic Company'").fetchone()
check("valid standard import confirmation",response.status_code==302 and company is not None)
check("admin company detail works",admin.get(f"/company/{company['id']}").status_code==200)
check("ungranted company is hidden",user.get(f"/company/{company['id']}").status_code==404)
check("search still finds allowed company",f'href="/company/{company["id"]}"'.encode() in admin.get("/search?q=Synthetic").data)

mapped=A.IMPORTS/"abcdef012345_mapped.xlsx"
workbook(mapped,row=("Mapped Company","Mapped Contact"))
review=admin.get("/admin/imports/mapped/security_test/review",query_string={"pending":mapped.name})
check("valid mapped preview",review.status_code==200)
plan=re.search(rb'name="plan" value="([^"]+)"',review.data).group(1).decode()
response=admin.post("/admin/imports/mapped/security_test/confirm",data={"pending":mapped.name,"plan":plan,"csrf_token":token(admin)})
with A.db() as con: mapped_row=con.execute("SELECT id FROM companies WHERE company_name='Mapped Company'").fetchone()
check("valid mapped confirmation",response.status_code==200 and mapped_row is not None)

# Dangerous workbook headers and values are neutralized in the actual CSV response.
headers=["=formula","+formula","-formula","@formula","\tformula","\rformula","Normal Unicode 世界"]
with A.db() as con:
    con.execute("UPDATE companies SET source_data=? WHERE id=?",(json.dumps({h:"=value" for h in headers}),company["id"]))
export=admin.get("/admin/export/companies.csv")
rows=list(csv.reader(io.StringIO(export.get_data(as_text=True).lstrip("\ufeff"))))
check("CSV headers neutralized",all("'"+h in rows[0] for h in headers[:-1]))
check("normal CSV header preserved",headers[-1] in rows[0])
check("CSV values remain neutralized",all(r[8:]==["'=value"]*len(headers) for r in rows[1:] if r[2]=="Synthetic Company"))
check("export remains no-store",export.headers["Cache-Control"]=="no-store")
for path in ("/search","/account/password","/admin/users",f"/company/{company['id']}","/not-found"):
    check("sensitive HTML no-store "+path,admin.get(path).headers.get("Cache-Control")=="no-store")
anon=A.app.test_client()
check("login is no-store",anon.get("/login").headers.get("Cache-Control")=="no-store")
check("static caching preserved",anon.get("/static/js/app.js").headers.get("Cache-Control")!="no-store")

# Inject unexpected failures at all four import boundaries and retain safe validation.
detail="Synthetic private path C:/private/internal-file.xlsx"
log_stream=io.StringIO(); handler=logging.StreamHandler(log_stream); logging.getLogger().addHandler(handler)
def safe_failure(response):
    html=response.get_data(as_text=True)
    reference=re.search(r"Reference: ([0-9a-f]{12})",html)
    check("unexpected import details hidden",detail not in html and reference is not None)
    check("reference correlates with log",reference.group(1) in log_stream.getvalue())
before=snapshot()
with patch.object(A,"import_workbook",side_effect=RuntimeError(detail)):
    safe_failure(admin.post("/admin/imports",data={"file":(io.BytesIO(pending.read_bytes()),"failure.xlsx"),"csrf_token":token(admin)},follow_redirects=True))
check("failed preview preserves database",snapshot()==before)
with patch.object(A,"import_workbook",side_effect=ValueError("Missing required column(s): company_name")):
    response=admin.post("/admin/imports",data={"file":(io.BytesIO(pending.read_bytes()),"missing.xlsx"),"csrf_token":token(admin)},follow_redirects=True)
    check("known validation stays actionable",b"Missing required column(s): company_name" in response.data and b"Reference:" not in response.data)
with patch.object(A,"import_workbook",side_effect=RuntimeError(detail)):
    safe_failure(admin.post("/admin/imports/upload/confirm",data={"pending":pending.name,"csrf_token":token(admin)},follow_redirects=True))
workbook(mapped)
with patch.object(A.profile_import,"analyze",side_effect=RuntimeError(detail)):
    safe_failure(admin.get("/admin/imports/mapped/security_test/review",query_string={"pending":mapped.name},follow_redirects=True))
workbook(mapped)
with patch.object(A.profile_import,"apply",side_effect=RuntimeError(detail)):
    safe_failure(admin.post("/admin/imports/mapped/security_test/confirm",data={"pending":mapped.name,"plan":"synthetic","csrf_token":token(admin)},follow_redirects=True))
logging.getLogger().removeHandler(handler); handler.close()

response=admin.post("/account/password",data={"current_password":"Synthetic-Admin-Password!","new_password":"Synthetic-Changed-Password!",
                    "confirm_password":"Synthetic-Changed-Password!","csrf_token":token(admin)})
check("valid CSRF password change",response.status_code==302)
login("admin","Synthetic-Changed-Password!")
check("valid CSRF logout",admin.post("/logout",data={"csrf_token":token(admin)}).status_code==302 and admin.get("/search").status_code==302)
print(f"{checks} checks passed; synthetic artifacts retained at {TMP}")
