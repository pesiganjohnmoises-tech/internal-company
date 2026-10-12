"""Security controls on disposable code copies and synthetic data only.

Run: python tests/test_security_controls.py
Includes the Release 1 workflow checks. Never calls /deploy or reads live data.
"""
import concurrent.futures
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import runpy
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch

SRC=Path(__file__).resolve().parent.parent
state=runpy.run_path(str(SRC/"tests/test_security_release1.py"))
A=state["A"]; check=state["check"]; token=state["token"]; login=state["login"]
TMP=state["TMP"]; security=A.security

def clear_events():
    with A.db() as con: con.execute("DELETE FROM security_events")

def fail(client,name="guessed",ip="192.0.2.1",password="wrong"):
    csrf_token=token(client,"/login")
    return client.post("/login",data={"username":name,"password":password,"csrf_token":csrf_token},environ_overrides={"REMOTE_ADDR":ip})

clear_events()
valid=login("admin","Synthetic-Changed-Password!")
fail(valid,"admin",password="wrong")
check("verified login clears account/pair history",valid.post("/login",data={"username":"admin","password":"Synthetic-Changed-Password!","csrf_token":token(valid,"/login")}).status_code==302)
with A.db() as con:
    check("previous IP failures retained after valid login",con.execute("SELECT COUNT(*) FROM security_events WHERE scope='auth-ip'").fetchone()[0]==1)
    check("account failures cleared after valid login",con.execute("SELECT COUNT(*) FROM security_events WHERE scope='auth-account'").fetchone()[0]==0)
clear_events()
client=A.app.test_client()
for _ in range(5): check("pair attempts admitted",fail(client).status_code==200)
response=fail(client)
check("sixth pair attempt blocked with retry timing",response.status_code==429 and 1<=int(response.headers["Retry-After"])<=900)
check("other account on same IP remains usable",fail(client,"different").status_code==200)
check("same account from another IP remains below short account limit",fail(client,ip="192.0.2.2").status_code==200)
with A.db() as con: con.execute("UPDATE security_events SET expires=0")
check("expired counters allow retry",fail(client).status_code==200)

clear_events()
with patch.object(A,"check_password_hash",return_value=False):
    for n in range(30): check("IP failures admitted",fail(client,f"name{n}").status_code==200)
    check("31st IP failure blocked across accounts",fail(client,"another").status_code==429)
clear_events()
with patch.object(A,"check_password_hash",return_value=False):
    for n in range(20): check("rotating IP failures admitted",fail(client,"target",f"198.51.100.{n+1}").status_code==200)
    response=fail(client,"target","198.51.100.100")
    check("account limit stops rotating IPs for at most 60 seconds",response.status_code==429 and int(response.headers["Retry-After"])<=60)

# Reservations atomically admit only five contenders, even in separate Python workers.
clear_events()
worker="""import json,sys
from utils.security import reserve
token,retry=reserve(sys.argv[1],[("worker-check","same-key",5,900)])
print(json.dumps(bool(token)))
"""
def reserve_worker(_):
    result=subprocess.run([sys.executable,"-c",worker,str(A.DB)],cwd=TMP,capture_output=True,text=True,check=True)
    return json.loads(result.stdout)
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: admitted=list(pool.map(reserve_worker,range(12)))
check("shared atomic reservations across processes",sum(admitted)==5)
check("counter persists in fresh process",reserve_worker(None) is False)
with patch.object(security,"MAX_EVENTS",5):
    check("counter storage is bounded",security.reserve(A.DB,[("new-key","new",1,60)])[1]==60)

# Database contention rejects expensive work instead of bypassing limits.
clear_events()
csrf_token=token(client,"/login")
with A.db() as locked:
    locked.execute("BEGIN IMMEDIATE")
    response=client.post("/login",data={"username":"guessed","password":"wrong","csrf_token":csrf_token})
    check("real SQLite lock returns retryable rejection",response.status_code==503 and response.headers["Retry-After"]=="5")
with patch.object(A.security,"reserve",side_effect=sqlite3.OperationalError("synthetic busy")),patch.object(A,"check_password_hash") as hashing:
    response=client.post("/login",data={"username":"guessed","password":"wrong","csrf_token":csrf_token})
    check("busy limiter fails closed",response.status_code==503 and response.headers["Retry-After"]=="5" and not hashing.called)
clear_events()
with patch.object(A,"check_password_hash") as hashing:
    check("oversized password rejected before hashing",fail(client,password="x"*257).status_code==400 and not hashing.called)
    check("oversized username rejected before hashing",fail(client,name="x"*65).status_code==400 and not hashing.called)

# Untrusted headers, malformed headers, and proxy chains cannot forge the leftmost client IP.
with patch.object(A,"CLIENT_IP_HEADER","X-Forwarded-For"),patch.object(A,"TRUSTED_PROXIES",(ipaddress.ip_network("10.0.0.0/24"),)):
    for peer,header,expected in (("192.0.2.1","203.0.113.99","192.0.2.1"),
                                 ("10.0.0.1","203.0.113.99, 192.0.2.1","192.0.2.1"),
                                 ("10.0.0.1","192.0.2.1, 10.0.0.2","192.0.2.1"),
                                 ("10.0.0.1","malformed","10.0.0.1")):
        with A.app.test_request_context(headers={"X-Forwarded-For":header},environ_base={"REMOTE_ADDR":peer}):
            check("proxy trust "+header,A.client_ip()==expected)

clear_events()
one=login("admin","Synthetic-Changed-Password!")
two=login("admin","Synthetic-Changed-Password!")
copied=one.get_cookie("session").value
check("logout succeeds",one.post("/logout",data={"csrf_token":token(one)}).status_code==302)
replay=A.app.test_client(); replay.set_cookie("session",copied)
check("copied cookie revoked after logout",replay.get("/search").status_code==302)
check("logout does not revoke another browser",two.get("/search").status_code==200)
with A.db() as con: uid=con.execute("SELECT id FROM users WHERE username='kharla'").fetchone()[0]
victim=login("kharla","Synthetic-Reset-Password!")
old_cookie=victim.get_cookie("session").value
for status in ("DISABLED","ACTIVE"):
    two.post(f"/admin/users/{uid}",data={"role":"USER","status":status,"current_password":"Synthetic-Changed-Password!","csrf_token":token(two)})
replay.set_cookie("session",old_cookie)
check("disable and re-enable never revives old cookie",replay.get("/search").status_code==302)
victim=login("kharla","Synthetic-Reset-Password!")
two.post(f"/admin/users/{uid}",data={"role":"FULL_ACCESS","status":"ACTIVE","current_password":"Synthetic-Changed-Password!","csrf_token":token(two)})
check("role change revokes user sessions",victim.get("/search").status_code==302)
legacy=A.app.test_client()
with legacy.session_transaction() as sess:
    sess.update(uid=uid,pwv="legacy",started=1,seen=1)
check("legacy cookie requires fresh sign-in",legacy.get("/search").status_code==302)
for field,age in (("seen",A.SESSION_IDLE+1),("started",A.SESSION_MAX+1)):
    victim=login("kharla","Synthetic-Reset-Password!")
    with victim.session_transaction() as sess: sid=security.opaque(sess["sid"])
    with A.db() as con: con.execute(f"UPDATE security_sessions SET {field}=? WHERE token_hash=?",(A.time.time()-age,sid))
    check("server-side "+field+" timeout enforced",victim.get("/search").status_code==302)

# Keep admin self-change validation and throttling after staff self-service removal.
clear_events()
for data,expected in (({"current_password":"incorrect","new_password":"New-Synthetic-Password!","confirm_password":"New-Synthetic-Password!"},"administrator password"),
                      ({"current_password":"Synthetic-Changed-Password!","new_password":"short","confirm_password":"short"},"at least 12"),
                      ({"current_password":"Synthetic-Changed-Password!","new_password":"New-Synthetic-Password!","confirm_password":"Different-Synthetic-Password!"},"do not match")):
    response=two.post("/account/password",data={**data,"csrf_token":token(two)},follow_redirects=True)
    check("admin own-password validation "+expected,response.status_code==200 and expected in response.get_data(as_text=True))
clear_events()
for _ in range(5): two.post("/account/password",data={"current_password":"wrong","csrf_token":token(two)})
check("admin current-password guessing throttled",two.post("/account/password",data={"current_password":"Synthetic-Changed-Password!","csrf_token":token(two)}).status_code==429)
clear_events()

# Existing records/grants remain unchanged; init must not regrant the former named starter account.
with A.db() as con:
    con.execute("DELETE FROM user_country_access WHERE user_id=?",(uid,))
    con.execute("DELETE FROM user_company_access WHERE user_id=?",(uid,))
    con.execute("UPDATE users SET updated_at=created_at WHERE id=?",(uid,))
    before=[tuple(r) for r in con.execute("SELECT id,username,password_hash,role,status FROM users ORDER BY id")]
A.init_db()
with A.db() as con:
    check("existing users preserved during additive initialization",before==[tuple(r) for r in con.execute("SELECT id,username,password_hash,role,status FROM users ORDER BY id")])
    check("startup does not silently grant directory access",con.execute("SELECT COUNT(*) FROM user_country_access WHERE user_id=?",(uid,)).fetchone()[0]==0 and con.execute("SELECT COUNT(*) FROM user_company_access WHERE user_id=?",(uid,)).fetchone()[0]==0)

# Model an existing pre-upgrade database without the new tables, using only synthetic rows.
legacy_db=TMP/"synthetic-pre-upgrade.db"
with A.db() as source,sqlite3.connect(legacy_db) as target:
    tables=source.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'security_%'").fetchall()
    for table in tables: target.execute(table["sql"])
    for table in tables:
        rows=source.execute(f'SELECT * FROM "{table["name"]}"').fetchall()
        if rows:
            placeholders=','.join('?' for _ in rows[0])
            target.executemany(f'INSERT INTO "{table["name"]}" VALUES({placeholders})',[tuple(r) for r in rows])
    old_users=target.execute("SELECT id,username,password_hash,role,status FROM users ORDER BY id").fetchall()
    old_counts={table["name"]:target.execute(f'SELECT COUNT(*) FROM "{table["name"]}"').fetchone()[0] for table in tables}
with patch.object(A,"DB",legacy_db): A.init_db()
with sqlite3.connect(legacy_db) as migrated:
    check("pre-upgrade migration preserves accounts",migrated.execute("SELECT id,username,password_hash,role,status FROM users ORDER BY id").fetchall()==old_users)
    check("pre-upgrade migration preserves records and grants",all(migrated.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]==count for name,count in old_counts.items()))
    check("pre-upgrade migration adds security tables",len(migrated.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'security_%'").fetchall())==2)
    check("pre-upgrade migration keeps foreign keys valid",migrated.execute("PRAGMA foreign_key_check").fetchall()==[])

# Rate limits are independent per individual; static files and logout stay usable.
clear_events()
for _ in range(120): response=two.get("/search")
check("normal search allowance",response.status_code==200)
check("excessive directory requests limited",two.get("/search").status_code==429)
check("static assets remain available",two.get("/static/js/app.js").status_code==200)
fresh=login("kharla","Synthetic-Reset-Password!")
check("other user has independent allowance",fresh.get("/search").status_code==200)
clear_events()
for _ in range(6): response=two.get("/admin/export/companies.csv")
check("export allowance works",response.status_code==200)
check("excessive exports limited",two.get("/admin/export/companies.csv").status_code==429)
clear_events()
with A.db() as con: cid=con.execute("SELECT id FROM countries LIMIT 1").fetchone()[0]
for _ in range(20): two.post("/admin/imports/preview",data={"filename":"missing.xlsx","csrf_token":token(two)})
check("import work limited before parsing",two.post("/admin/imports/preview",data={"filename":"missing.xlsx","csrf_token":token(two)}).status_code==429)

# Fresh subprocess copies prove bootstrap/production configuration failure without touching real files.
def startup(extra):
    folder=Path(tempfile.mkdtemp(prefix="directory-startup-test-"))
    shutil.copy2(TMP/"app.py",folder/"app.py")
    shutil.copytree(TMP/"utils",folder/"utils",ignore=shutil.ignore_patterns("__pycache__"))
    env=os.environ.copy()
    for name in ("ADMIN_PASSWORD","SECRET_KEY","COOKIE_SECURE","FLASK_DEBUG","CLIENT_IP_HEADER","TRUSTED_PROXY_CIDRS"):
        env.pop(name,None)
    env.update({"APP_ENV":"development",**extra})
    script="import app; print(app.app.config['SESSION_COOKIE_SECURE']); print(app.db().execute('SELECT COUNT(*) FROM users').fetchone()[0])"
    return subprocess.run([sys.executable,"-c",script],cwd=folder,env=env,capture_output=True,text=True),folder
result,folder=startup({})
check("fresh bootstrap requires explicit password",result.returncode!=0 and "requires ADMIN_PASSWORD" in result.stderr)
result,folder=startup({"ADMIN_PASSWORD":"Synthetic-Bootstrap-Password!"})
check("fresh bootstrap creates only admin",result.returncode==0 and result.stdout.strip().splitlines()==["False","1"])
result,_=startup({"APP_ENV":"production","ADMIN_PASSWORD":"Synthetic-Bootstrap-Password!"})
check("production rejects missing persistent key",result.returncode!=0 and "Production requires" in result.stderr)
result,_=startup({"APP_ENV":"production","ADMIN_PASSWORD":"Synthetic-Bootstrap-Password!","SECRET_KEY":"s"*48})
check("production defaults to secure cookies",result.returncode==0 and result.stdout.strip().splitlines()==["True","1"])
for unsafe in ({"COOKIE_SECURE":"0"},{"FLASK_DEBUG":"1"}):
    result,_=startup({"APP_ENV":"production","ADMIN_PASSWORD":"Synthetic-Bootstrap-Password!","SECRET_KEY":"s"*48,**unsafe})
    check("production rejects unsafe configuration "+str(list(unsafe)),result.returncode!=0)
result,_=startup({"ADMIN_PASSWORD":"Synthetic-Bootstrap-Password!","CLIENT_IP_HEADER":"X-Real-IP"})
check("proxy header requires explicit trust",result.returncode!=0 and "TRUSTED_PROXY_CIDRS" in result.stderr)
print(f"{check.__globals__['checks']} total checks passed; synthetic artifacts retained.")
