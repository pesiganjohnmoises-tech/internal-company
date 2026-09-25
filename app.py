import os, re, io, csv, sqlite3, logging, secrets, time, hashlib, hmac, subprocess, unicodedata
from functools import wraps
from pathlib import Path
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, Response, g
from markupsafe import Markup, escape
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_wtf.csrf import CSRFProtect
from utils.importer import import_workbook, column_map, detect_country, backfill_source_data, agent_id_from_source, utcnow
from utils.fields import build_company_detail
from utils import dashboard
from utils.status import agent_status, country_code

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
# Rotated at 1 MB with 3 old files kept, so the log cannot fill the disk; the activity panel reads the current file.
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[RotatingFileHandler(ROOT/"app.log",maxBytes=1024*1024,backupCount=3,encoding="utf-8")])
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
            ("users","first_name","TEXT"),("users","last_name","TEXT"),("companies","agent_id","TEXT"),
            ("user_country_access","all_companies","INTEGER NOT NULL DEFAULT 0"),("users","last_login_at","TEXT")
        ]:
            existing={r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            if column not in existing: c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        c.execute("CREATE INDEX IF NOT EXISTS idx_companies_agent_id ON companies(agent_id COLLATE NOCASE)")
        # Companies imported before agent_id existed take it from their stored XLSX row.
        filled=[(aid,r["id"]) for r in c.execute("SELECT id,source_data FROM companies WHERE agent_id IS NULL AND source_data IS NOT NULL")
                if (aid:=agent_id_from_source(r["source_data"]))]
        if filled: c.executemany("UPDATE companies SET agent_id=? WHERE id=?",filled); logging.info("Backfilled agent_id rows=%s",len(filled))
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
            for r in c.execute("SELECT id FROM countries").fetchall(): c.execute("INSERT OR IGNORE INTO user_country_access(user_id,country_id) VALUES(?,?)",(kh["id"],r["id"]))
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
    # Looked up once per request (the context processor and each decorator ask again).
    if "current_user" not in g: g.current_user=load_current_user()
    return g.current_user
def load_current_user():
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
    """WHERE condition for the companies a user may see. A User needs the country, and either the company
    or the country's "all companies" grant (which also covers companies added by later imports)."""
    if user["role"] in ("ADMIN","FULL_ACCESS"): return "1=1",[]
    return f"""EXISTS(SELECT 1 FROM user_country_access uca WHERE uca.user_id=? AND uca.country_id={alias}.country_id
      AND (uca.all_companies=1 OR EXISTS(SELECT 1 FROM user_company_access uca2 WHERE uca2.user_id=? AND uca2.company_id={alias}.id)))""",[user["id"],user["id"]]
@app.context_processor
def inject():
    # An error page must still render when the database itself is the problem.
    try: return {"user":current_user()}
    except sqlite3.Error: return {"user":None}
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
            with db() as c: c.execute("UPDATE users SET last_login_at=? WHERE id=?",(now(),u["id"]))
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
SEARCH_FIELDS=["company_name","network","city","state","country","contact_type","name","job_position","email","phone","landline_no","address"]
def visible_fields(user):
    """Every core column, plus the sales columns granted to a User (all of them for Admin/Full Access).
    Search and the company page use the same list, so a user can search anything they can see."""
    if user["role"] in ("ADMIN","FULL_ACCESS"): return list(FIELD_COLUMNS)
    with db() as c: granted={r["field_name"] for r in c.execute("SELECT field_name FROM user_field_access WHERE user_id=?",(user["id"],))}
    return list(FIELD_COLUMNS)+[f for f in SALES_FIELDS if f in granted]
@app.template_filter("hl")
def highlight(text,q):
    """Escape text and wrap the search terms in <mark>."""
    text="" if text is None else str(text)
    terms=sorted({t for t in (q or "").split() if t},key=len,reverse=True)
    if not terms: return escape(text)
    parts=re.split("("+"|".join(re.escape(t) for t in terms)+")",text,flags=re.I)
    return Markup("".join(f"<mark>{escape(p)}</mark>" if i%2 else str(escape(p)) for i,p in enumerate(parts)))
# Stored times are UTC; pages show Philippine Time. The Philippines has no daylight saving, so PHT is
# always UTC+8 (a fixed offset also avoids needing the tzdata package on Windows).
PHT=timezone(timedelta(hours=8),"PHT")
@app.template_filter("when")
def when(value):
    """A stored UTC time ("2026-09-24T03:04:10Z") in Philippine Time ("24 Sep 2026, 11:04 PHT"), UTC value on hover."""
    if not value: return "—"
    try: d=datetime.fromisoformat(str(value).rstrip("Z")).replace(tzinfo=timezone.utc).astimezone(PHT)
    except ValueError: return value
    return Markup(f'<time datetime="{escape(value)}" title="{escape(value)} (UTC)">{d.day} {d:%b %Y, %H:%M} PHT</time>')
@app.template_filter("source_name")
def source_name(value):
    """Workbook name without the random prefix uploads are stored under ("dc6a3c9498c6_india.xlsx" -> "india.xlsx")."""
    return re.sub(r"^[0-9a-f]{12}_","",value or "")
@app.template_filter("tel")
def tel_href(value):
    """tel: link for the first number in a phone cell ("+65 8000 0001 / +65 ..."), or "" if it isn't one."""
    digits=re.sub(r"[^\d+]","",re.split(r"[;,/\n]",str(value or ""))[0])
    return "tel:"+digits if len(re.sub(r"\D","",digits))>=6 else ""
@app.template_filter("first_email")
def first_email(value):
    """The first email address in a cell, or ""."""
    m=re.search(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+",str(value or ""))
    return m.group(0) if m else ""
def arg_int(name):
    """Non-negative integer query parameter; 0 when missing or invalid."""
    try: return max(0,int(request.args.get(name) or 0))
    except ValueError: return 0
def country_options(user):
    """Countries the user can browse: every country for Admin/Full Access, granted ones for a User."""
    with db() as c:
        if user["role"] in ("ADMIN","FULL_ACCESS"): return [r["name"] for r in c.execute("SELECT name FROM countries ORDER BY name")]
        return [r["name"] for r in c.execute("SELECT cn.name FROM countries cn JOIN user_country_access a ON a.country_id=cn.id WHERE a.user_id=? ORDER BY cn.name",(user["id"],))]
def page_links(page,pages):
    """Page numbers around the current page, with None marking a gap."""
    keep=sorted({1,pages,*range(max(1,page-1),min(pages,page+1)+1)})
    out=[]
    for n in keep:
        if out and n-out[-1]>1: out.append(None)
        out.append(n)
    return out
PAGE_SIZE=20; MAX_QUERY=200; MAX_TERMS=8
@app.route("/account/password",methods=["GET","POST"])
@login_required
def account_password():
    u=current_user()
    if request.method=="POST":
        # Wrong current passwords count toward the same throttle as failed sign-ins.
        key=(u["username"].lower(),client_ip()); t=time.time()
        recent=[x for x in FAILED_LOGINS.get(key,[]) if t-x<LOGIN_WINDOW]
        if len(recent)>=LOGIN_LIMIT:
            flash("Too many incorrect attempts. Try again in 15 minutes.","error"); return render_template("account_password.html"),429
        with db() as c: row=c.execute("SELECT id,password_hash FROM users WHERE id=?",(u["id"],)).fetchone()
        current=request.form.get("current_password",""); new=request.form.get("new_password","")
        if not check_password_hash(row["password_hash"],current):
            FAILED_LOGINS[key]=recent+[t]; logging.warning("Failed password change user=%s ip=%s",u["username"],key[1])
            flash("Your current password is incorrect.","error")
        elif len(new)<12: flash("The new password must be at least 12 characters.","error")
        elif new!=request.form.get("confirm_password",""): flash("The new passwords do not match.","error")
        elif new==current: flash("Choose a password different from your current one.","error")
        else:
            # updated_at is left alone: it marks admin edits (see the kharla rule in init_db).
            with db() as c:
                c.execute("UPDATE users SET password_hash=? WHERE id=?",(generate_password_hash(new),u["id"]))
                start_session(c.execute("SELECT id,password_hash FROM users WHERE id=?",(u["id"],)).fetchone())
            logging.info("User %s changed own password",u["username"])
            flash("Password changed. Any other devices signed in to your account have been signed out.","success")
            return redirect(url_for("account_password"))
    return render_template("account_password.html")
@app.route("/search")
@login_required
def search():
    q=request.args.get("q","").strip()[:MAX_QUERY]
    country=request.args.get("country","").strip()[:100]
    # Each word adds a condition per column; long queries are cut to keep SQL small and fast.
    terms=q.split()[:MAX_TERMS]
    # Older links use ?offset=<row>; they open the page holding that row.
    page=arg_int("page") or arg_int("offset")//PAGE_SIZE+1
    user=current_user(); clause,params=access_sql(user); fields=visible_fields(user)
    # Only match on columns this user may see, so search cannot reveal hidden values.
    # Agent ID is shown to everyone who can open the company, so it is always searchable.
    cols=["co.agent_id"]+[FIELD_COLUMNS[f] for f in SEARCH_FIELDS if f in fields]
    rows=[]; previews={}; total=0; pages=1
    # A country on its own lists all of that country's companies the user may see.
    if q or country:
        where=clause
        if country: where+=" AND cn.name=? COLLATE NOCASE"; params.append(country)
        for term in terms:
            if not cols: where+=" AND 0"; continue
            where+=" AND ("+" OR ".join(col+" LIKE ? COLLATE NOCASE" for col in cols)+")"
            params+=["%"+term+"%"]*len(cols)
        # One result per company; a term must match the company or one of its contacts.
        sql=f"""SELECT co.id,co.agent_id,co.source_data,co.company_name,co.network,co.city,co.state,cn.name country,COUNT(ct.id) matched,MIN(ct.id) preview_id,
            (SELECT COUNT(*) FROM contacts c2 WHERE c2.company_id=co.id) contacts
            FROM companies co JOIN countries cn ON cn.id=co.country_id LEFT JOIN contacts ct ON ct.company_id=co.id WHERE {where} GROUP BY co.id"""
        with db() as c:
            total=c.execute("SELECT COUNT(*) FROM ("+sql+")",params).fetchone()[0]
            pages=max(1,-(-total//PAGE_SIZE)); page=min(page,pages)
            # An exact Agent ID match (e.g. SGP001) comes before partial ones (SGP0010, SGP0011...).
            exact=f"co.agent_id COLLATE NOCASE IN ({','.join('?'*len(terms))}) DESC," if terms else ""
            rows=c.execute(sql+f" ORDER BY {exact}cn.name,co.company_name COLLATE NOCASE LIMIT ? OFFSET ?",params+terms+[PAGE_SIZE,(page-1)*PAGE_SIZE]).fetchall()
            rows=[{**dict(r),"status":agent_status(r["source_data"]),"code":country_code(r["country"])} for r in rows]
            ids=[r["preview_id"] for r in rows if r["preview_id"]]
            if ids: previews={r["id"]:r for r in c.execute(f"SELECT id,name,job_position,email,phone,landline_no FROM contacts WHERE id IN ({','.join('?'*len(ids))})",ids)}
    start=(page-1)*PAGE_SIZE+1 if total else 0
    tiles=[]; attention=None
    if not q and not country:
        # Start page: the countries this user can browse, with how many companies each holds.
        clause,params=access_sql(user)
        with db() as c:
            tiles=[{"name":r["name"],"code":country_code(r["name"]),"companies":r["n"]} for r in c.execute(
                f"SELECT cn.name,COUNT(co.id) n FROM companies co JOIN countries cn ON cn.id=co.country_id WHERE {clause} GROUP BY cn.id ORDER BY cn.name",params)]
            if user["role"]=="ADMIN":
                try: attention=dashboard.attention(c)["total"]
                except Exception: logging.exception("Attention count failed")
    return render_template("search.html",rows=rows,previews=previews,q=q,country=country,countries=country_options(user),tiles=tiles,attention=attention,terms_cut=len(q.split())>len(terms),fields=fields,field_labels=FIELD_LABELS,
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
    detail=build_company_detail(co,contacts,lambda key: full or key not in SALES_FIELDS or key in fields)
    ref=request.referrer or ""
    back=ref if ref.startswith(request.host_url.rstrip("/")+url_for("search")) else url_for("search")
    return render_template("company.html",company=co,contacts=contacts,fields=fields,field_labels=FIELD_LABELS,detail=detail,back_url=back,
                           status=agent_status(co["source_data"]),code=country_code(co["country"]),
                           source_name=source_name(co["source_file"]),sales_labels=SALES_FIELDS)
@app.errorhandler(403)
def forbidden(e): return render_template("error.html",message="You do not have permission to access this page."),403
@app.errorhandler(404)
def notfound(e): return render_template("error.html",message="The requested directory record was not found."),404
@app.errorhandler(400)
def bad_request(e): return render_template("error.html",message="The form could not be processed, possibly because it expired. Go back, reload the page and try again."),400
@app.errorhandler(413)
def too_large(e): return render_template("error.html",message="That file is larger than the 16 MB upload limit."),413
@app.errorhandler(500)
def server_error(e): return render_template("error.html",message="Something went wrong on our side. The error has been logged; please try again."),500

DEFAULT_PASSWORDS={"admin":"ChangeMe-Admin-2026!","kharla":"ChangeMe-Kharla-2026!"}
# Password hashing is deliberately slow (~0.13 s), so each stored hash is checked once, not on every overview load.
DEFAULT_PW_CHECKED={}
def uses_default_password(username,password_hash):
    key=(username,password_hash)
    if key not in DEFAULT_PW_CHECKED: DEFAULT_PW_CHECKED[key]=check_password_hash(password_hash,DEFAULT_PASSWORDS[username])
    return DEFAULT_PW_CHECKED[key]
@app.route("/admin")
@admin_required
def admin_dashboard():
    with db() as c:
        stats={"users":c.execute("SELECT COUNT(*) FROM users").fetchone()[0],"countries":c.execute("SELECT COUNT(*) FROM countries").fetchone()[0],"companies":c.execute("SELECT COUNT(*) FROM companies").fetchone()[0],"contacts":c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]}
        imports=c.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 8").fetchall()
        # Accounts still using the documented initial passwords.
        default_pw=[u for u in DEFAULT_PASSWORDS if (r:=c.execute("SELECT password_hash FROM users WHERE username=? AND status='ACTIVE'",(u,)).fetchone()) and uses_default_password(u,r["password_hash"])]
        def panel(name,fn):
            # A failing panel shows an error box instead of breaking the whole overview.
            try: return {"data":fn(c),"error":None}
            except Exception:
                logging.exception("Dashboard panel failed: %s",name); return {"data":None,"error":True}
        panels={"attention":panel("attention",dashboard.attention),"quality":panel("quality",dashboard.data_quality),"breakdown":panel("breakdown",dashboard.breakdown),
                "sales":panel("sales",lambda c: dashboard.sales_coverage(c,SALES_FIELDS)),
                "access":panel("access",lambda c: dashboard.access_overview(c,SALES_FIELDS,access_sql)),
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
    q=request.args.get("q","").strip()
    with db() as c:
        total=c.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        rows=[]
        for r in c.execute("SELECT id,username,first_name,last_name,role,status,created_at,last_login_at FROM users ORDER BY username COLLATE NOCASE").fetchall():
            clause,params=access_sql(r)
            rows.append({**dict(r),"display_name":display_name(r),
                         "visible":total if r["role"]!="USER" else c.execute(f"SELECT COUNT(*) FROM companies co WHERE {clause}",params).fetchone()[0]})
    shown=[r for r in rows if q.lower() in " ".join((r["username"],r["display_name"],r["role"],r["status"])).lower()] if q else rows
    return render_template("admin/users.html",users=shown,q=q,total_users=len(rows),total_companies=total)
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
    return render_template("admin/user_edit.html",target=None,countries=get_countries(),selected_countries=[],selected_all=[],selected_companies=[])
def access_summary(c,uid):
    """Grant counts for the audit log line written when a user is saved."""
    return {"countries":c.execute("SELECT COUNT(*) FROM user_country_access WHERE user_id=?",(uid,)).fetchone()[0],
            "whole countries":c.execute("SELECT COUNT(*) FROM user_country_access WHERE user_id=? AND all_companies=1",(uid,)).fetchone()[0],
            "companies":c.execute("SELECT COUNT(*) FROM user_company_access WHERE user_id=?",(uid,)).fetchone()[0],
            "sales":sorted(r["field_name"] for r in c.execute("SELECT field_name FROM user_field_access WHERE user_id=?",(uid,)) if r["field_name"] in SALES_FIELDS)}
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
            before=access_summary(c,uid)
            c.execute("UPDATE users SET first_name=?,last_name=?,role=?,status=?,updated_at=? WHERE id=?",(*names,role,status,now(),uid))
            if pw: c.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?",(generate_password_hash(pw),now(),uid))
            # A password change ends every session of that account; keep the admin's own session going.
            if pw and uid==admin["id"]: start_session(c.execute("SELECT id,password_hash FROM users WHERE id=?",(uid,)).fetchone())
            c.execute("DELETE FROM user_country_access WHERE user_id=?",(uid,)); c.execute("DELETE FROM user_company_access WHERE user_id=?",(uid,))
            known={r["id"] for r in c.execute("SELECT id FROM countries")}
            # "All companies" for a country also grants the country itself.
            ticked={int(x) for x in request.form.getlist("countries") if x.isdigit() and int(x) in known}
            whole={int(x) for x in request.form.getlist("all_companies") if x.isdigit() and int(x) in known}
            whole|={x for x in ticked if request.form.get(f"scope-{x}")=="all"}
            countries=sorted(ticked|whole)
            companies=[int(x) for x in request.form.getlist("companies") if x.isdigit()]
            for x in countries: c.execute("INSERT OR IGNORE INTO user_country_access(user_id,country_id,all_companies) VALUES(?,?,?)",(uid,x,int(x in whole)))
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
            after=access_summary(c,uid)
            changes=[f"{k} {a}->{b}" for k,a,b in (("role",target["role"],role),("status",target["status"],status)) if a!=b]
            changes+=[f"{k} {before[k]}->{after[k]}" for k in before if before[k]!=after[k]]
            if names!=((target["first_name"] or ""),(target["last_name"] or "")): changes.append("name updated")
            if pw: changes.append("password reset")
            logging.info("Admin %s updated user %s (id=%s): %s",admin["username"],target["username"],uid,"; ".join(changes) or "no changes")
            return redirect(url_for("user_edit",uid=uid))
        allco=c.execute("SELECT co.id,co.company_name,co.country_id,cn.name country FROM companies co JOIN countries cn ON cn.id=co.country_id ORDER BY cn.name,co.company_name").fetchall()
        grants=c.execute("SELECT country_id,all_companies FROM user_country_access WHERE user_id=?",(uid,)).fetchall()
        sc={r["country_id"] for r in grants}; sa={r["country_id"] for r in grants if r["all_companies"]}
        sm={r["company_id"] for r in c.execute("SELECT company_id FROM user_company_access WHERE user_id=?",(uid,))}
        ss={r["field_name"] for r in c.execute("SELECT field_name FROM user_field_access WHERE user_id=?",(uid,))}&set(SALES_FIELDS)
    return render_template("admin/user_edit.html",target=target,countries=get_countries(),selected_countries=sc,selected_all=sa,selected_companies=sm,companies=allco,sales_fields=SALES_FIELDS,selected_sales=ss)
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
# Imports replace a country's whole dataset, so each one is previewed first (a trial run that is rolled
# back) and the database is backed up just before the real import.
BACKUPS=ROOT/"backups"; BACKUP_KEEP=10
PENDING=re.compile(r"[0-9a-f]{12}_[A-Za-z0-9_.-]+\.xlsx")
def backup_db(label):
    """Consistent copy of the live database in backups/; only the newest BACKUP_KEEP are kept."""
    BACKUPS.mkdir(exist_ok=True)
    dest=BACKUPS/f"directory-{time.strftime('%Y%m%d-%H%M%S',time.gmtime())}-{secrets.token_hex(2)}-{re.sub(r'[^A-Za-z0-9]+','-',label).strip('-')[:40]}.db"
    src=sqlite3.connect(DB); out=sqlite3.connect(dest)
    try: src.backup(out)
    finally: out.close(); src.close()
    for old in sorted(BACKUPS.glob("directory-*.db"))[:-BACKUP_KEEP]: old.unlink(missing_ok=True)
    return dest.name
def record_import(source,country,status,result=None):
    with db() as c:
        if result: c.execute("INSERT INTO imports(source_file,country,status,records_processed,records_imported,duplicates_found,errors_found,imported_at) VALUES(?,?,?,?,?,?,?,?)",
                             (source,country,status,result["processed"],result["imported"],result["duplicates"],len(result["errors"]),now()))
        else: c.execute("INSERT INTO imports(source_file,country,status,errors_found,imported_at) VALUES(?,?,?,?,?)",(source,country,status,1,now()))
def run_import(path,source,country):
    """Back up, then replace the country's data with the workbook; the outcome goes to import history."""
    try: backup=backup_db(country)
    except Exception:
        logging.exception("Backup before import failed file=%s",source)
        flash("Import cancelled: the database backup could not be written, so nothing was changed.","error"); return
    try: result=import_workbook(path,DB,country,replace_country=True)
    except Exception as e:
        logging.exception("Import failed file=%s",source); record_import(source,country,"FAILED")
        flash(f"{source}: import rejected; previous active dataset was kept. {e}","error"); return
    record_import(source,country,"COMPLETED",result); ch=result["changes"]
    logging.info("Admin %s imported %s rows=%s added=%s removed=%s backup=%s",current_user()["username"],source,result["imported"],len(ch["added"]),len(ch["removed"]),backup)
    flash(f"{result['country']} replaced from {source}: {result['imported']} contact rows, {len(ch['added'])} companies added, {len(ch['removed'])} removed. Backup saved as {backup}.","success")
    if result["errors"]: flash("Import issues: "+"; ".join(result["errors"][:5]),"error")
def preview_import(path,source,country,confirm):
    """What importing would change, without changing anything. confirm: hidden fields for the confirm form."""
    try: result=import_workbook(path,DB,country,replace_country=True,dry_run=True)
    except Exception as e:
        logging.warning("Import preview failed file=%s: %s",source,e)
        flash(f"{source} cannot be imported: {e}","error"); return None
    with db() as c:
        cid=c.execute("SELECT id FROM countries WHERE name=? COLLATE NOCASE",(result["country"],)).fetchone()
        # Users who get this country company by company will not see companies the import adds.
        partial=[] if not cid else c.execute("""SELECT u.id,u.username FROM users u JOIN user_country_access a ON a.user_id=u.id
            WHERE a.country_id=? AND a.all_companies=0 AND u.role='USER' ORDER BY u.username COLLATE NOCASE""",(cid["id"],)).fetchall()
        stale=next((f for f in dashboard.import_health(c,DATA)["stale"] if f["name"]==source),None) if "filename" in confirm else None
    return render_template("admin/import_preview.html",r=result,source=source,partial=partial,stale=stale,confirm=confirm)
@app.route("/admin/imports",methods=["GET","POST"])
@admin_required
def imports_page():
    if request.method=="POST":
        uploaded=request.files.get("file")
        if not uploaded or not uploaded.filename.lower().endswith(".xlsx"):
            flash("Choose a valid .xlsx file.","error"); return redirect(url_for("imports_page"))
        name=secure_filename(uploaded.filename)
        if not name or ".." in name: abort(400)
        dest=IMPORTS/(secrets.token_hex(6)+"_"+name); uploaded.save(dest)
        country=(request.form.get("country") or "").strip() or detect_country(name)
        page=preview_import(dest,name,country,{"action":url_for("import_upload_confirm"),"pending":dest.name,"country":country})
        if page: return page
        dest.unlink(missing_ok=True); return redirect(url_for("imports_page"))
    # Uploads previewed but never confirmed or cancelled are removed after a day.
    for old in IMPORTS.glob("*.xlsx"):
        if time.time()-old.stat().st_mtime>86400: old.unlink(missing_ok=True)
    files=sorted(p.name for p in DATA.glob("*.xlsx"))
    with db() as c: history=c.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 30").fetchall()
    backups=[{"name":b.name,"size":b.stat().st_size} for b in sorted(BACKUPS.glob("directory-*.db"),reverse=True)] if BACKUPS.exists() else []
    return render_template("admin/imports.html",files=files,history=history,backups=backups)
def seed_path(filename):
    path=(DATA/filename).resolve()
    if path.parent!=DATA.resolve() or path.suffix.lower()!=".xlsx" or not path.exists(): abort(400)
    return path
def pending_path():
    name=request.form.get("pending","")
    if not PENDING.fullmatch(name) or not (IMPORTS/name).exists(): abort(400)
    return IMPORTS/name
@app.post("/admin/imports/preview")
@admin_required
def import_preview():
    filename=request.form.get("filename","")
    return preview_import(seed_path(filename),filename,detect_country(filename),{"action":url_for("import_seed"),"filename":filename}) or redirect(url_for("imports_page"))
@app.post("/admin/imports/seed")
@admin_required
def import_seed():
    filename=request.form.get("filename","")
    run_import(seed_path(filename),filename,detect_country(filename))
    return redirect(url_for("imports_page"))
@app.post("/admin/imports/upload/confirm")
@admin_required
def import_upload_confirm():
    path=pending_path()
    try: run_import(path,path.name[13:],(request.form.get("country") or "").strip() or detect_country(path.name[13:]))
    finally: path.unlink(missing_ok=True)
    return redirect(url_for("imports_page"))
@app.post("/admin/imports/discard")
@admin_required
def import_discard():
    if PENDING.fullmatch(request.form.get("pending","")): (IMPORTS/request.form["pending"]).unlink(missing_ok=True)
    flash("Import cancelled. Nothing was changed.","success")
    return redirect(url_for("imports_page"))
@app.route("/admin/database")
@admin_required
def database_status():
    # The database summary now heads the Records page; old links land there.
    return redirect(url_for("admin_records"))
RECORDS_PAGE=50
@app.route("/admin/records")
@admin_required
def admin_records():
    q=request.args.get("q","").strip()[:MAX_QUERY]; country=request.args.get("country","").strip()[:100]
    where="(co.company_name LIKE ? COLLATE NOCASE OR co.agent_id LIKE ? COLLATE NOCASE)"; params=["%"+q+"%"]*2
    if country: where+=" AND cn.name=? COLLATE NOCASE"; params.append(country)
    base=f"FROM companies co JOIN countries cn ON cn.id=co.country_id WHERE {where}"
    with db() as c:
        total=c.execute(f"SELECT COUNT(*) {base}",params).fetchone()[0]
        pages=max(1,-(-total//RECORDS_PAGE)); page=min(max(1,arg_int("page")),pages)
        rows=c.execute(f"""SELECT co.id,co.agent_id,co.company_name,cn.name country,co.network,co.source_file,co.source_row,co.updated_at,
            (SELECT COUNT(*) FROM contacts ct WHERE ct.company_id=co.id) contacts {base}
            ORDER BY cn.name,co.company_name COLLATE NOCASE LIMIT ? OFFSET ?""",params+[RECORDS_PAGE,(page-1)*RECORDS_PAGE]).fetchall()
        summary={"companies":c.execute("SELECT COUNT(*) FROM companies").fetchone()[0],"contacts":c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
                 "countries":c.execute("SELECT COUNT(*) FROM countries").fetchone()[0],"last":c.execute("SELECT imported_at FROM imports WHERE status='COMPLETED' ORDER BY id DESC LIMIT 1").fetchone()}
    summary["size"]=DB.stat().st_size if DB.exists() else 0
    start=(page-1)*RECORDS_PAGE+1 if total else 0
    return render_template("admin/records.html",rows=rows,q=q,country=country,countries=[r["name"] for r in get_countries()],summary=summary,
                           total=total,page=page,pages=pages,page_links=page_links(page,pages),start=start,end=start+len(rows)-1 if rows else 0)
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
