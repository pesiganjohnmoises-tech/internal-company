import os, re, io, csv, sqlite3, logging, secrets, time, hashlib, hmac, subprocess, unicodedata
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, Response
from markupsafe import Markup, escape
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_wtf.csrf import CSRFProtect
from utils.importer import import_workbook, column_map, detect_country, backfill_source_data, utcnow
from utils.fields import build_company_detail
from utils import dashboard

ROOT=Path(__file__).resolve().parent
DB=ROOT/"directory.db"; DATA=ROOT/"data"; IMPORTS=ROOT/"imports"
DATA.mkdir(exist_ok=True); IMPORTS.mkdir(exist_ok=True)
def load_secret_key():
    if os.environ.get("SECRET_KEY"): return os.environ["SECRET_KEY"]
    # No SECRET_KEY set: generate one once and reuse it, so sessions survive restarts.
    path=ROOT/".secret_key"
    try:
        if path.exists() and path.read_text().strip(): return path.read_text().strip()
        key=secrets.token_hex(32); path.write_text(key)
        try: os.chmod(path,0o600)
        except OSError: pass
        return key
    except OSError: return secrets.token_hex(32)
app=Flask(__name__)
app.secret_key=load_secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE","0")=="1",
                  MAX_CONTENT_LENGTH=16*1024*1024)
csrf=CSRFProtect(app)
logging.basicConfig(filename=ROOT/"app.log",level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
# Every character str.splitlines() breaks on; escaped so user input cannot start a forged log line.
LOG_BREAKS=str.maketrans({ch:f"\\x{ord(ch):02x}" if ord(ch)<256 else f"\\u{ord(ch):04x}" for ch in "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"})
class SingleLineLog(logging.Filter):
    """The admin overview reads app.log, so each message must stay on one line."""
    def filter(self,record):
        msg=record.getMessage()
        if msg!=msg.translate(LOG_BREAKS): record.msg=msg.translate(LOG_BREAKS); record.args=()
        return True
for handler in logging.getLogger().handlers: handler.addFilter(SingleLineLog())
# Optional: behind a proxy that puts the visitor's address in a header (e.g. X-Real-IP), name it here
# so sign-in throttling sees real client addresses instead of the proxy's.
CLIENT_IP_HEADER=os.environ.get("CLIENT_IP_HEADER","").strip()
def client_ip():
    return (request.headers.get(CLIENT_IP_HEADER,"").split(",")[0].strip() if CLIENT_IP_HEADER else "") or request.remote_addr
@app.after_request
def security_headers(resp):
    # Pages must not be framed by other sites (clickjacking); the rest only tightens browser defaults.
    resp.headers.setdefault("X-Frame-Options","DENY")
    resp.headers.setdefault("Content-Security-Policy","frame-ancestors 'none'")
    resp.headers.setdefault("X-Content-Type-Options","nosniff")
    resp.headers.setdefault("Referrer-Policy","same-origin")
    return resp
def db():
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row; con.execute("PRAGMA foreign_keys=ON"); return con
def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'USER', status TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS countries(id INTEGER PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE, code TEXT, source_file TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY, country_id INTEGER NOT NULL REFERENCES countries(id), company_name TEXT NOT NULL, network TEXT, contact_type TEXT, address TEXT, source_file TEXT, source_row INTEGER, created_at TEXT, updated_at TEXT, UNIQUE(country_id,company_name COLLATE NOCASE));
        CREATE TABLE IF NOT EXISTS contacts(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE, name TEXT, job_position TEXT, email TEXT, phone TEXT, address TEXT, source_file TEXT, source_row INTEGER, created_at TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS user_country_access(user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,country_id INTEGER REFERENCES countries(id) ON DELETE CASCADE,PRIMARY KEY(user_id,country_id));
        CREATE TABLE IF NOT EXISTS user_company_access(user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,PRIMARY KEY(user_id,company_id));
        CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY,source_file TEXT,country TEXT,status TEXT,records_processed INTEGER DEFAULT 0,records_imported INTEGER DEFAULT 0,duplicates_found INTEGER DEFAULT 0,errors_found INTEGER DEFAULT 0,imported_at TEXT);
        CREATE TABLE IF NOT EXISTS user_field_access(user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, field_name TEXT NOT NULL, PRIMARY KEY(user_id,field_name));
        CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(company_name COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(name COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_contacts_phone ON contacts(phone);
        """)
        for table, column, declaration in [
            ("companies","city","TEXT"),("companies","state","TEXT"),
            ("contacts","contact_type","TEXT"),("contacts","landline_no","TEXT"),
            ("companies","source_data","TEXT"),("contacts","source_data","TEXT"),
            ("users","first_name","TEXT"),("users","last_name","TEXT")
        ]:
            existing={r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            if column not in existing: c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        # Earlier imports ran without foreign keys and left rows pointing at deleted companies/users.
        # They are never displayed; removing them is a no-op once the database is clean.
        removed=sum(c.execute(sql).rowcount for sql in (
            "DELETE FROM contacts WHERE company_id NOT IN (SELECT id FROM companies)",
            "DELETE FROM user_company_access WHERE company_id NOT IN (SELECT id FROM companies) OR user_id NOT IN (SELECT id FROM users)",
            "DELETE FROM user_country_access WHERE country_id NOT IN (SELECT id FROM countries) OR user_id NOT IN (SELECT id FROM users)",
            "DELETE FROM user_field_access WHERE user_id NOT IN (SELECT id FROM users)"))
        if removed: logging.info("Removed orphaned rows=%s",removed)
        # The initial accounts are created on first start only; an admin deleting one must not bring it
        # back with the documented default password.
        if not c.execute("SELECT 1 FROM users").fetchone():
            stamp=now()
            c.execute("INSERT INTO users(username,password_hash,role,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",("admin",generate_password_hash(os.environ.get("ADMIN_PASSWORD","ChangeMe-Admin-2026!")),"ADMIN","ACTIVE",stamp,stamp))
            c.execute("INSERT INTO users(username,password_hash,role,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",("kharla",generate_password_hash(os.environ.get("KHARLA_PASSWORD","ChangeMe-Kharla-2026!")),"USER","ACTIVE",stamp,stamp))
        # Kharla is a standard user with all directory access until an admin edits her account
        # (updated_at moves on); from then on the admin's access settings are never overridden.
        kh=c.execute("SELECT id FROM users WHERE username='kharla' AND role='USER' AND updated_at IS created_at").fetchone()
        if kh:
            for r in c.execute("SELECT id FROM countries").fetchall(): c.execute("INSERT OR IGNORE INTO user_country_access VALUES(?,?)",(kh["id"],r["id"]))
            for r in c.execute("SELECT id FROM companies").fetchall(): c.execute("INSERT OR IGNORE INTO user_company_access VALUES(?,?)",(kh["id"],r["id"]))
    # Records imported before full-row capture get their XLSX columns filled in place, keeping IDs.
    try:
        filled=backfill_source_data(DB,DATA)
        if filled: logging.info("Backfilled source_data rows=%s",filled)
    except Exception: logging.exception("source_data backfill failed")
def now(): return utcnow()
NAME_MAX=60
def display_name(u):
    """First + last name; the username when neither is set."""
    return " ".join(n for n in ((u["first_name"] or "").strip(),(u["last_name"] or "").strip()) if n) or u["username"]
def name_fields(form):
    """(first, last) from a submitted form, or None when either is too long."""
    names=tuple(" ".join(form.get(k,"").split()) for k in ("first_name","last_name"))
    return None if any(len(n)>NAME_MAX for n in names) else names
SESSION_IDLE=2*60*60; SESSION_MAX=12*60*60
def password_version(password_hash):
    """Short fingerprint of the stored hash: changing the password ends every existing session."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]
def start_session(u):
    session.clear(); t=int(time.time())
    session.update(uid=u["id"],pwv=password_version(u["password_hash"]),started=t,seen=t)
def current_user():
    if "uid" not in session: return None
    t=int(time.time())
    if t-session.get("seen",0)>SESSION_IDLE or t-session.get("started",0)>SESSION_MAX: session.clear(); return None
    with db() as c: u=c.execute("SELECT id,username,first_name,last_name,role,status,password_hash FROM users WHERE id=?",(session["uid"],)).fetchone()
    if not u or session.get("pwv")!=password_version(u["password_hash"]): session.clear(); return None
    if t-session["seen"]>60: session["seen"]=t
    return {**{k:u[k] for k in ("id","username","first_name","last_name","role","status")},"display_name":display_name(u)}
def login_required(fn):
    @wraps(fn)
    def wrap(*a,**kw):
        u=current_user()
        if not u or u["status"]!="ACTIVE": session.clear(); return redirect(url_for("login"))
        return fn(*a,**kw)
    return wrap
def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrap(*a,**kw):
        if current_user()["role"]!="ADMIN": abort(403)
        return fn(*a,**kw)
    return wrap
def access_sql(user,alias="co"):
    if user["role"] in ("ADMIN","FULL_ACCESS"): return "1=1",[]
    return f"""EXISTS(SELECT 1 FROM user_country_access uca WHERE uca.user_id=? AND uca.country_id={alias}.country_id)
      AND EXISTS(SELECT 1 FROM user_company_access uca2 WHERE uca2.user_id=? AND uca2.company_id={alias}.id)""",[user["id"],user["id"]]
@app.context_processor
def inject(): return {"user":current_user()}
@app.route("/")
def home(): return redirect(url_for("search")) if current_user() else redirect(url_for("login"))
# Failed sign-ins kept in memory per process for 15 minutes: 5 per (username, IP) locks that pair,
# and 30 per IP locks that address across all usernames (guessing many accounts).
FAILED_LOGINS={}; LOGIN_LIMIT=5; IP_LOGIN_LIMIT=30; LOGIN_WINDOW=15*60
# Checked when the username is unknown, so both cases take the same time (no username discovery).
DUMMY_HASH=generate_password_hash(secrets.token_hex(16))
@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        username=request.form.get("username","").strip(); ip=client_ip()
        key=(username.lower(),ip); ipkey=("*",ip); t=time.time()
        recent=[x for x in FAILED_LOGINS.get(key,[]) if t-x<LOGIN_WINDOW]
        ip_recent=[x for x in FAILED_LOGINS.get(ipkey,[]) if t-x<LOGIN_WINDOW]
        if len(recent)>=LOGIN_LIMIT or len(ip_recent)>=IP_LOGIN_LIMIT:
            logging.warning("Throttled login username=%s ip=%s",username,ip)
            flash("Too many failed sign-in attempts. Try again in 15 minutes.","error")
            return render_template("login.html"),429
        with db() as c: u=c.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE",(username,)).fetchone()
        valid=check_password_hash(u["password_hash"] if u else DUMMY_HASH,request.form.get("password",""))
        if u and valid and u["status"]=="ACTIVE":
            FAILED_LOGINS.pop(key,None)
            start_session(u); logging.info("Successful login user=%s",u["username"]); return redirect(url_for("admin_dashboard") if u["role"]=="ADMIN" else url_for("search"))
        if len(FAILED_LOGINS)>10000:
            for k in [k for k,v in FAILED_LOGINS.items() if t-v[-1]>=LOGIN_WINDOW]: del FAILED_LOGINS[k]
        FAILED_LOGINS[key]=recent+[t]; FAILED_LOGINS[ipkey]=ip_recent+[t]
        logging.warning("Failed login username=%s ip=%s",username,ip); flash("Invalid username or password.","error")
    return render_template("login.html")
@app.post("/logout")
@login_required
def logout(): session.clear(); return redirect(url_for("login"))
FIELD_COLUMNS={"company_name":"co.company_name","city":"co.city","state":"co.state","country":"cn.name","network":"co.network","contact_type":"ct.contact_type","name":"ct.name","job_position":"ct.job_position","email":"ct.email","phone":"ct.phone","landline_no":"ct.landline_no","address":"ct.address"}
FIELD_LABELS={"company_name":"Company Name Entity","city":"City","state":"State","country":"Country","network":"Network","contact_type":"Contact Type","name":"Name","job_position":"Job Position","email":"Email","phone":"Phone","landline_no":"Landline No","address":"Address"}
# XLSX sales columns (field keys as built by utils.fields.columns) granted per USER via user_field_access.
SALES_FIELDS={"x_kgsales":"Kargosmart Sales","x_pcsales":"Panda Cargo Sales","x_tisales":"Tri-Star Logistics Sales","x_kwsales":"KirinWorld Sales"}
SEARCH_FIELDS=["company_name","network","city","state","country","contact_type","name","job_position","email","phone","address"]
def visible_fields(user):
    if user["role"] in ("ADMIN","FULL_ACCESS"): return list(FIELD_COLUMNS)
    with db() as c: granted=[r["field_name"] for r in c.execute("SELECT field_name FROM user_field_access WHERE user_id=?",(user["id"],))]
    # Sales grants are added on top of the field list; they never replace its defaults.
    fields=[f for f in granted if f not in SALES_FIELDS]; sales=[f for f in granted if f in SALES_FIELDS]
    return (fields or ["company_name","city","state","country","contact_type","name","job_position","email","phone","address"])+sales
@app.template_filter("hl")
def highlight(text,q):
    """Escape text and wrap the search terms in <mark>."""
    text="" if text is None else str(text)
    terms=sorted({t for t in (q or "").split() if t},key=len,reverse=True)
    if not terms: return escape(text)
    parts=re.split("("+"|".join(re.escape(t) for t in terms)+")",text,flags=re.I)
    return Markup("".join(f"<mark>{escape(p)}</mark>" if i%2 else str(escape(p)) for i,p in enumerate(parts)))
def page_links(page,pages):
    """Page numbers around the current page, with None marking a gap."""
    keep=sorted({1,pages,*range(max(1,page-1),min(pages,page+1)+1)})
    out=[]
    for n in keep:
        if out and n-out[-1]>1: out.append(None)
        out.append(n)
    return out
PAGE_SIZE=20; MAX_QUERY=200; MAX_TERMS=8
@app.route("/search")
@login_required
def search():
    q=request.args.get("q","").strip()[:MAX_QUERY]
    # Each word adds a condition per column; long queries are cut to keep SQL small and fast.
    terms=q.split()[:MAX_TERMS]
    def arg_int(name):
        try: return max(0,int(request.args.get(name) or 0))
        except ValueError: return 0
    # Older links use ?offset=<row>; they open the page holding that row.
    page=arg_int("page") or arg_int("offset")//PAGE_SIZE+1
    user=current_user(); clause,params=access_sql(user); fields=visible_fields(user)
    # Only match on columns this user may see, so search cannot reveal hidden values.
    cols=[FIELD_COLUMNS[f] for f in SEARCH_FIELDS if f in fields]
    rows=[]; previews={}; total=0; pages=1
    if q:
        where=clause
        for term in terms:
            if not cols: where+=" AND 0"; continue
            where+=" AND ("+" OR ".join(col+" LIKE ? COLLATE NOCASE" for col in cols)+")"
            params+=["%"+term+"%"]*len(cols)
        # One result per company; a term must match the company or one of its contacts.
        sql=f"""SELECT co.id,co.company_name,co.network,co.city,co.state,cn.name country,COUNT(ct.id) matched,MIN(ct.id) preview_id,
            (SELECT COUNT(*) FROM contacts c2 WHERE c2.company_id=co.id) contacts
            FROM companies co JOIN countries cn ON cn.id=co.country_id LEFT JOIN contacts ct ON ct.company_id=co.id WHERE {where} GROUP BY co.id"""
        with db() as c:
            total=c.execute("SELECT COUNT(*) FROM ("+sql+")",params).fetchone()[0]
            pages=max(1,-(-total//PAGE_SIZE)); page=min(page,pages)
            rows=c.execute(sql+" ORDER BY cn.name,co.company_name COLLATE NOCASE LIMIT ? OFFSET ?",params+[PAGE_SIZE,(page-1)*PAGE_SIZE]).fetchall()
            ids=[r["preview_id"] for r in rows if r["preview_id"]]
            if ids: previews={r["id"]:r for r in c.execute(f"SELECT id,name,job_position,email,phone FROM contacts WHERE id IN ({','.join('?'*len(ids))})",ids)}
    start=(page-1)*PAGE_SIZE+1 if total else 0
    return render_template("search.html",rows=rows,previews=previews,q=q,terms_cut=len(q.split())>len(terms),fields=fields,field_labels=FIELD_LABELS,
                           total=total,page=page,pages=pages,page_links=page_links(page,pages),start=start,end=start+len(rows)-1 if rows else 0)
@app.route("/company/<int:company_id>")
@login_required
def company(company_id):
    u=current_user(); clause,params=access_sql(u); fields=visible_fields(u)
    with db() as c:
        co=c.execute(f"SELECT co.*,cn.name country FROM companies co JOIN countries cn ON cn.id=co.country_id WHERE co.id=? AND {clause}",[company_id]+params).fetchone()
        if not co: abort(404)
        contacts=c.execute("SELECT * FROM contacts WHERE company_id=? ORDER BY id",(company_id,)).fetchall()
    full=u["role"] in ("ADMIN","FULL_ACCESS")
    # Users see every column Admin/Full Access see, except sales columns not granted on the Admin Page.
    if not full: fields=list(FIELD_COLUMNS)+[f for f in fields if f in SALES_FIELDS]
    detail=build_company_detail(co,contacts,lambda key: full or key not in SALES_FIELDS or key in fields)
    ref=request.referrer or ""
    back=ref if ref.startswith(request.host_url.rstrip("/")+url_for("search")) else url_for("search")
    return render_template("company.html",company=co,contacts=contacts,fields=fields,field_labels=FIELD_LABELS,detail=detail,back_url=back)
@app.errorhandler(403)
def forbidden(e): return render_template("error.html",message="You do not have permission to access this page."),403
@app.errorhandler(404)
def notfound(e): return render_template("error.html",message="The requested directory record was not found."),404

DEFAULT_PASSWORDS={"admin":"ChangeMe-Admin-2026!","kharla":"ChangeMe-Kharla-2026!"}
@app.route("/admin")
@admin_required
def admin_dashboard():
    with db() as c:
        stats={"users":c.execute("SELECT COUNT(*) FROM users").fetchone()[0],"countries":c.execute("SELECT COUNT(*) FROM countries").fetchone()[0],"companies":c.execute("SELECT COUNT(*) FROM companies").fetchone()[0],"contacts":c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]}
        imports=c.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 8").fetchall()
        # Accounts still using the documented initial passwords.
        default_pw=[u for u,p in DEFAULT_PASSWORDS.items() if (r:=c.execute("SELECT password_hash FROM users WHERE username=? AND status='ACTIVE'",(u,)).fetchone()) and check_password_hash(r["password_hash"],p)]
        def panel(name,fn):
            # A failing panel shows an error box instead of breaking the whole overview.
            try: return {"data":fn(c),"error":None}
            except Exception:
                logging.exception("Dashboard panel failed: %s",name); return {"data":None,"error":True}
        panels={"attention":panel("attention",dashboard.attention),"quality":panel("quality",dashboard.data_quality),"breakdown":panel("breakdown",dashboard.breakdown),
                "sales":panel("sales",lambda c: dashboard.sales_coverage(c,SALES_FIELDS)),
                "access":panel("access",lambda c: dashboard.access_overview(c,SALES_FIELDS)),
                "imports":panel("imports",lambda c: dashboard.import_health(c,DATA)),
                "activity":panel("activity",lambda c: dashboard.activity(c,ROOT/"app.log"))}
    return render_template("admin/dashboard.html",stats=stats,imports=imports,default_pw=default_pw,panels=panels)
@app.route("/admin/export/<kind>.csv")
@admin_required
def admin_export(kind):
    if kind not in ("companies","contacts"): abort(404)
    with db() as c: header,rows=dashboard.export_rows(c,kind)
    buf=io.StringIO(); w=csv.writer(buf); w.writerow(header); w.writerows(rows)
    logging.info("Admin %s exported %s rows=%s",current_user()["username"],kind,len(rows))
    # UTF-8 BOM so Excel detects the encoding of non-ASCII names.
    return Response("\ufeff"+buf.getvalue(),mimetype="text/csv",
                    headers={"Content-Disposition":f'attachment; filename="{kind}-{utcnow()[:10]}.csv"',"Cache-Control":"no-store"})
@app.route("/admin/users")
@admin_required
def users():
    with db() as c: rows=c.execute("SELECT id,username,role,status,created_at FROM users ORDER BY username").fetchall()
    return render_template("admin/users.html",users=rows)
@app.route("/admin/users/create",methods=["GET","POST"])
@admin_required
def user_create():
    if request.method=="POST":
        username=request.form.get("username","").strip(); password=request.form.get("password",""); names=name_fields(request.form)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}",username) or len(password)<12:
            flash("Use a 3–64 character username and a password of at least 12 characters.","error")
        elif not names:
            flash(f"First and last name must be at most {NAME_MAX} characters.","error")
        else:
            try:
                role=request.form.get("role","USER") if request.form.get("role") in ("USER","ADMIN","FULL_ACCESS") else "USER"
                with db() as c: c.execute("INSERT INTO users(username,first_name,last_name,password_hash,role,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(username,*names,generate_password_hash(password),role,"ACTIVE",now(),now()))
                logging.info("Admin %s created user %s role=%s",current_user()["username"],username,role)
                flash("User created. Assign access below.","success"); return redirect(url_for("user_edit",uid=get_user_id(username)))
            except sqlite3.IntegrityError: flash("That username already exists.","error")
    return render_template("admin/user_edit.html",target=None,countries=get_countries(),selected_countries=[],selected_companies=[])
def get_user_id(username):
    with db() as c: return c.execute("SELECT id FROM users WHERE username=?",(username,)).fetchone()["id"]
def get_countries():
    with db() as c: return c.execute("SELECT * FROM countries ORDER BY name").fetchall()
@app.route("/admin/users/<int:uid>",methods=["GET","POST"])
@admin_required
def user_edit(uid):
    with db() as c:
        target=c.execute("SELECT id,username,first_name,last_name,role,status FROM users WHERE id=?",(uid,)).fetchone()
        if not target: abort(404)
        if request.method=="POST":
            role=request.form.get("role","USER"); status=request.form.get("status","ACTIVE")
            if role not in ("USER","ADMIN","FULL_ACCESS") or status not in ("ACTIVE","DISABLED"): abort(400)
            pw=request.form.get("password",""); names=name_fields(request.form)
            # Validate everything before writing, so a rejected save changes nothing.
            if pw and len(pw)<12:
                flash("Password must be at least 12 characters. No changes were saved.","error"); return redirect(url_for("user_edit",uid=uid))
            if not names:
                flash(f"First and last name must be at most {NAME_MAX} characters. No changes were saved.","error"); return redirect(url_for("user_edit",uid=uid))
            admin=current_user()
            if uid==admin["id"] and (role!="ADMIN" or status!="ACTIVE"):
                flash("You cannot remove admin access from, or disable, your own account. No changes were saved.","error"); return redirect(url_for("user_edit",uid=uid))
            before={"countries":c.execute("SELECT COUNT(*) FROM user_country_access WHERE user_id=?",(uid,)).fetchone()[0],
                    "companies":c.execute("SELECT COUNT(*) FROM user_company_access WHERE user_id=?",(uid,)).fetchone()[0],
                    "sales":sorted(r["field_name"] for r in c.execute("SELECT field_name FROM user_field_access WHERE user_id=?",(uid,)) if r["field_name"] in SALES_FIELDS)}
            c.execute("UPDATE users SET first_name=?,last_name=?,role=?,status=?,updated_at=? WHERE id=?",(*names,role,status,now(),uid))
            if pw: c.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?",(generate_password_hash(pw),now(),uid))
            # A password change ends every session of that account; keep the admin's own session going.
            if pw and uid==admin["id"]: start_session(c.execute("SELECT id,password_hash FROM users WHERE id=?",(uid,)).fetchone())
            c.execute("DELETE FROM user_country_access WHERE user_id=?",(uid,)); c.execute("DELETE FROM user_company_access WHERE user_id=?",(uid,))
            known={r["id"] for r in c.execute("SELECT id FROM countries")}
            countries=[int(x) for x in request.form.getlist("countries") if x.isdigit() and int(x) in known]
            companies=[int(x) for x in request.form.getlist("companies") if x.isdigit()]
            for x in countries: c.execute("INSERT OR IGNORE INTO user_country_access VALUES(?,?)",(uid,x))
            # Only store companies belonging to selected countries; query validation prevents bypass.
            if countries:
                marks=",".join("?"*len(countries))
                valid=c.execute(f"SELECT id FROM companies WHERE country_id IN ({marks})",countries).fetchall()
                validids={r["id"] for r in valid}
                for x in companies:
                    if x in validids: c.execute("INSERT OR IGNORE INTO user_company_access VALUES(?,?)",(uid,x))
            # Only the four sales keys are replaced; any other user_field_access rows are kept.
            c.execute(f"DELETE FROM user_field_access WHERE user_id=? AND field_name IN ({','.join('?'*len(SALES_FIELDS))})",[uid,*SALES_FIELDS])
            for f in request.form.getlist("sales_fields"):
                if f in SALES_FIELDS: c.execute("INSERT OR IGNORE INTO user_field_access VALUES(?,?)",(uid,f))
            flash("User and access settings saved.","success")
            after={"countries":c.execute("SELECT COUNT(*) FROM user_country_access WHERE user_id=?",(uid,)).fetchone()[0],
                   "companies":c.execute("SELECT COUNT(*) FROM user_company_access WHERE user_id=?",(uid,)).fetchone()[0],
                   "sales":sorted(r["field_name"] for r in c.execute("SELECT field_name FROM user_field_access WHERE user_id=?",(uid,)) if r["field_name"] in SALES_FIELDS)}
            changes=[f"{k} {a}->{b}" for k,a,b in (("role",target["role"],role),("status",target["status"],status)) if a!=b]
            changes+=[f"{k} {before[k]}->{after[k]}" for k in before if before[k]!=after[k]]
            if names!=((target["first_name"] or ""),(target["last_name"] or "")): changes.append("name updated")
            if pw: changes.append("password reset")
            logging.info("Admin %s updated user %s (id=%s): %s",admin["username"],target["username"],uid,"; ".join(changes) or "no changes")
            return redirect(url_for("user_edit",uid=uid))
        allco=c.execute("SELECT co.id,co.company_name,co.country_id,cn.name country FROM companies co JOIN countries cn ON cn.id=co.country_id ORDER BY cn.name,co.company_name").fetchall()
        sc={r["country_id"] for r in c.execute("SELECT country_id FROM user_country_access WHERE user_id=?",(uid,))}
        sm={r["company_id"] for r in c.execute("SELECT company_id FROM user_company_access WHERE user_id=?",(uid,))}
        ss={r["field_name"] for r in c.execute("SELECT field_name FROM user_field_access WHERE user_id=?",(uid,))}&set(SALES_FIELDS)
    return render_template("admin/user_edit.html",target=target,countries=get_countries(),selected_countries=sc,selected_companies=sm,companies=allco,sales_fields=SALES_FIELDS,selected_sales=ss)
@app.post("/admin/users/<int:uid>/delete")
@admin_required
def user_delete(uid):
    if uid==current_user()["id"]: flash("You cannot delete your own account.","error")
    else:
        with db() as c:
            gone=c.execute("SELECT username FROM users WHERE id=?",(uid,)).fetchone()
            c.execute("DELETE FROM users WHERE id=?",(uid,))
        if gone: logging.info("Admin %s deleted user %s (id=%s)",current_user()["username"],gone["username"],uid)
        flash("User deleted.","success")
    return redirect(url_for("users"))
@app.route("/admin/imports",methods=["GET","POST"])
@admin_required
def imports_page():
    if request.method=="POST":
        uploaded=request.files.get("file")
        if not uploaded or not uploaded.filename.lower().endswith(".xlsx"):
            flash("Choose a valid .xlsx file.","error")
        else:
            name=secure_filename(uploaded.filename)
            if not name or ".." in name: abort(400)
            dest=IMPORTS/(secrets.token_hex(6)+"_"+name); uploaded.save(dest)
            country=(request.form.get("country") or "").strip() or detect_country(name)
            try:
                result=import_workbook(dest,DB,country,replace_country=True)
                with db() as c:
                    c.execute("INSERT INTO imports(source_file,country,status,records_processed,records_imported,duplicates_found,errors_found,imported_at) VALUES(?,?,?,?,?,?,?,?)",
                    (name,country,"COMPLETED",result["processed"],result["imported"],result["duplicates"],len(result["errors"]),now()))
                logging.info("Admin %s imported %s rows=%s",current_user()["username"],name,result["imported"])
                flash(f"Country dataset replaced: {result['imported']} rows loaded.", "success")
                if result["errors"]: flash("Import issues: "+"; ".join(result["errors"][:5]),"error")
            except Exception as e:
                logging.exception("Import failed file=%s",name)
                with db() as c: c.execute("INSERT INTO imports(source_file,country,status,errors_found,imported_at) VALUES(?,?,?,?,?)",(name,country,"FAILED",1,now()))
                flash(f"Upload rejected; previous active dataset was kept. {str(e)}","error")
            finally:
                try: dest.unlink()
                except OSError: pass
    files=sorted(p.name for p in DATA.glob("*.xlsx"))
    with db() as c: history=c.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 30").fetchall()
    return render_template("admin/imports.html",files=files,history=history)

@app.post("/admin/imports/seed")
@admin_required
def import_seed():
    filename=request.form.get("filename","")
    path=(DATA/filename).resolve()
    if path.parent!=DATA.resolve() or path.suffix.lower()!=".xlsx" or not path.exists(): abort(400)
    country=detect_country(filename)
    try: result=import_workbook(path,DB,country,replace_country=True)
    except Exception as e:
        logging.exception("Import failed file=%s",filename)
        with db() as c: c.execute("INSERT INTO imports(source_file,country,status,errors_found,imported_at) VALUES(?,?,?,?,?)",(filename,country,"FAILED",1,now()))
        flash(f"{filename}: import rejected; previous active dataset was kept. {str(e)}","error")
        return redirect(url_for("imports_page"))
    with db() as c:
        c.execute("INSERT INTO imports(source_file,country,status,records_processed,records_imported,duplicates_found,errors_found,imported_at) VALUES(?,?,?,?,?,?,?,?)",(filename,country,"COMPLETED",result["processed"],result["imported"],result["duplicates"],len(result["errors"]),now()))
    logging.info("Admin %s imported %s rows=%s",current_user()["username"],filename,result["imported"])
    flash(f"{filename}: replaced with {result['imported']} row records.","success")
    if result["errors"]: flash("Import issues: "+"; ".join(result["errors"][:5]),"error")
    return redirect(url_for("imports_page"))
@app.route("/admin/database")
@admin_required
def database_status():
    with db() as c:
        countries=c.execute("SELECT cn.name,COUNT(DISTINCT co.id) companies,COUNT(ct.id) contacts,MAX(co.updated_at) updated FROM countries cn LEFT JOIN companies co ON co.country_id=cn.id LEFT JOIN contacts ct ON ct.company_id=co.id GROUP BY cn.id ORDER BY cn.name").fetchall()
        last=c.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 1").fetchone()
    return render_template("admin/database.html",countries=countries,size=DB.stat().st_size if DB.exists() else 0,last=last)
@app.route("/admin/records")
@admin_required
def admin_records():
    q=request.args.get("q","").strip()
    with db() as c:
        rows=c.execute("SELECT co.company_name,cn.name country,co.network,co.contact_type,co.source_file,co.source_row FROM companies co JOIN countries cn ON cn.id=co.country_id WHERE co.company_name LIKE ? COLLATE NOCASE ORDER BY cn.name,co.company_name LIMIT 200",("%"+q+"%",)).fetchall()
    return render_template("admin/records.html",rows=rows,q=q)
# Auto-deploy: GitHub calls /deploy on each push; the route pulls the new code and touches the WSGI
# file so PythonAnywhere reloads. Disabled (404) unless DEPLOY_SECRET is set.
DEPLOY_SECRET=os.environ.get("DEPLOY_SECRET","")
DEPLOY_BRANCH=os.environ.get("DEPLOY_BRANCH","main")
DEPLOY_WSGI_PATH=os.environ.get("DEPLOY_WSGI_PATH","")
@app.route("/deploy",methods=["POST"])
@csrf.exempt
def deploy():
    if not DEPLOY_SECRET: abort(404)
    sig="sha256="+hmac.new(DEPLOY_SECRET.encode(),request.get_data(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig,request.headers.get("X-Hub-Signature-256","")):
        logging.warning("deploy: bad signature from %s",client_ip()); abort(403)
    if request.headers.get("X-GitHub-Event")=="ping": return "pong"
    if (request.get_json(silent=True) or {}).get("ref")!=f"refs/heads/{DEPLOY_BRANCH}": return "ignored: not the deploy branch"
    try: r=subprocess.run(["git","pull","--ff-only"],cwd=ROOT,capture_output=True,text=True,timeout=120)
    except (OSError,subprocess.TimeoutExpired) as e:
        logging.error("deploy: git pull failed: %s",e); return "git pull failed",500
    if r.returncode:
        logging.error("deploy: git pull failed: %s",(r.stderr or r.stdout).strip()); return "git pull failed",500
    logging.info("deploy: %s",r.stdout.strip())
    if DEPLOY_WSGI_PATH: Path(DEPLOY_WSGI_PATH).touch()
    return "deployed"
with app.app_context(): init_db()
if __name__=="__main__": app.run(debug=os.environ.get("FLASK_DEBUG")=="1")
