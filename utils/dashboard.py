"""Read-only summaries for the admin overview.

Each panel function takes an open connection (sqlite3.Row rows) and only reads. XLSX-only columns
come from companies.source_data and are matched by normalized header, like utils.fields does, so
dates and statuses are judged exactly as on the company page.
"""
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from utils.fields import load, is_empty, parse_date, tone, columns
from utils.importer import norm, detect_country

EXPIRY_SOON_DAYS=90

def companies_with_source(con):
    """[(row, {normalized header: value})] for every company."""
    rows=con.execute("SELECT co.id,co.company_name,co.network,co.source_data,cn.name country FROM companies co JOIN countries cn ON cn.id=co.country_id ORDER BY cn.name,co.company_name COLLATE NOCASE").fetchall()
    return [(r,{norm(k):v for k,v in load(r["source_data"]).items()}) for r in rows]

def item(r,detail,sort=None):
    return {"id":r["id"],"name":r["company_name"],"country":r["country"],"detail":detail,"sort":sort}

def attention(con,today=None):
    """Companies someone needs to act on: network memberships, KYC and agent status."""
    today=today or date.today(); soon=today+timedelta(days=EXPIRY_SOON_DAYS)
    out={"expired":[],"soon":[],"unreadable":[],"kyc_pending":[],"inactive":[]}
    for r,v in companies_with_source(con):
        raw=v.get("networkexpiry")
        if not is_empty(raw):
            d=parse_date(raw)
            if d is None: out["unreadable"].append(item(r,f"“{str(raw).strip()}”"))
            elif d<today: out["expired"].append(item(r,f"Expired {d.day} {d:%b %Y}",d))
            elif d<=soon: out["soon"].append(item(r,f"Expires {d.day} {d:%b %Y} · {(d-today).days} days",d))
        kyc=v.get("kycstatus")
        if not is_empty(kyc) and tone(str(kyc))=="warn": out["kyc_pending"].append(item(r,f"KYC {str(kyc).strip()}"))
        status=v.get("agentstatus")
        if not is_empty(status) and tone(str(status))=="bad": out["inactive"].append(item(r,f"Agent status {str(status).strip()}"))
    out["expired"].sort(key=lambda x:x["sort"],reverse=True); out["soon"].sort(key=lambda x:x["sort"])
    out["total"]=sum(len(v) for v in out.values())
    return out

def data_quality(con):
    """Gaps and duplicates in contact details, grouped by company."""
    rows=con.execute("""SELECT ct.id,ct.email,ct.phone,ct.landline_no,co.id company_id,co.company_name,cn.name country
        FROM contacts ct JOIN companies co ON co.id=ct.company_id JOIN countries cn ON cn.id=co.country_id
        ORDER BY cn.name,co.company_name COLLATE NOCASE,ct.id""").fetchall()
    companies={}; emails={}
    for r in rows:
        c=companies.setdefault(r["company_id"],{"r":r,"contacts":0,"no_email":0,"no_phone":0})
        c["contacts"]+=1
        if is_empty(r["email"]): c["no_email"]+=1
        else: emails.setdefault(r["email"].strip().lower(),[]).append(r)
        if is_empty(r["phone"]) and is_empty(r["landline_no"]): c["no_phone"]+=1
    def co_item(c,detail): return {"id":c["r"]["company_id"],"name":c["r"]["company_name"],"country":c["r"]["country"],"detail":detail}
    no_email_companies=[co_item(c,f"None of {c['contacts']} contact{'s' if c['contacts']!=1 else ''} has an email") for c in companies.values() if c["no_email"]==c["contacts"]]
    missing_email=[co_item(c,f"{c['no_email']} of {c['contacts']} contacts without email") for c in companies.values() if 0<c["no_email"]<c["contacts"]]
    duplicates=[]
    for email,rs in sorted(emails.items()):
        if len(rs)<2: continue
        names=sorted({r["company_name"] for r in rs})
        first=rs[0]
        duplicates.append({"id":first["company_id"],"name":email,"country":first["country"],
                           "detail":f"{len(rs)} contacts · "+(names[0] if len(names)==1 else f"{len(names)} companies: "+", ".join(names))})
    return {"contacts":len(rows),
            "no_email":sum(c["no_email"] for c in companies.values()),
            "no_phone":sum(c["no_phone"] for c in companies.values()),
            "no_email_companies":no_email_companies,"missing_email":missing_email,"duplicates":duplicates}

def breakdown(con):
    """Companies and contacts per country, and companies per network ("WCA, GLA" or "GAA and WCA" counts for each)."""
    countries=[dict(r) for r in con.execute("""SELECT cn.name,COUNT(DISTINCT co.id) companies,COUNT(ct.id) contacts,MAX(co.updated_at) updated
        FROM countries cn LEFT JOIN companies co ON co.country_id=cn.id LEFT JOIN contacts ct ON ct.company_id=co.id
        GROUP BY cn.id ORDER BY companies DESC,cn.name""")]
    counts={}; labels={}; none=0
    for r in con.execute("SELECT network FROM companies"):
        parts=[p.strip() for p in re.split(r"[,;/]|\s+and\s+|\s*&\s*",r["network"] or "",flags=re.I) if p.strip()]
        if not parts: none+=1
        for p in {p.lower():p for p in parts}.values():
            labels.setdefault(p.lower(),p); counts[p.lower()]=counts.get(p.lower(),0)+1
    networks=[{"name":labels[k],"companies":n} for k,n in sorted(counts.items(),key=lambda kv:(-kv[1],kv[0]))]
    total=sum(c["companies"] for c in countries)
    return {"countries":countries,"networks":networks,"no_network":none,"total":total,
            "max_country":max([c["companies"] for c in countries] or [1]) or 1,
            "max_network":max([n["companies"] for n in networks]+[none,1])}

def split_names(value):
    return [p.strip() for p in re.split(r"[,;/]|\s+and\s+|\s*&\s*",str(value or ""),flags=re.I) if p.strip()]

def sales_coverage(con,sales_fields):
    """Per sales column: companies with/without a rep, and reps by company count.
    sales_fields maps field keys ("x_kgsales") to labels, as in app.SALES_FIELDS."""
    companies=companies_with_source(con)
    out=[]
    for key,label in sales_fields.items():
        col=key[2:] if key.startswith("x_") else key
        reps={}; names={}; missing=[]
        for r,v in companies:
            people=split_names(v.get(col)) if not is_empty(v.get(col)) else []
            if not people: missing.append(item(r,f"No {label} rep"))
            for p in {p.lower():p for p in people}.values():
                names.setdefault(p.lower(),p); reps[p.lower()]=reps.get(p.lower(),0)+1
        ranked=[{"name":names[k],"companies":n} for k,n in sorted(reps.items(),key=lambda kv:(-kv[1],kv[0]))]
        out.append({"label":label,"assigned":len(companies)-len(missing),"missing":missing,"reps":ranked,"total":len(companies)})
    return out

def access_overview(con,sales_fields):
    """Every account with what it can see. A USER sees a company only with both its country and company granted."""
    total=con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    users=[]
    for u in con.execute("SELECT id,username,role,status FROM users ORDER BY username COLLATE NOCASE").fetchall():
        row={"id":u["id"],"username":u["username"],"role":u["role"],"status":u["status"],"total":total}
        if u["role"]=="USER":
            row["countries"]=con.execute("SELECT COUNT(*) FROM user_country_access WHERE user_id=?",(u["id"],)).fetchone()[0]
            row["visible"]=con.execute("""SELECT COUNT(*) FROM companies co WHERE EXISTS(SELECT 1 FROM user_company_access a WHERE a.user_id=? AND a.company_id=co.id)
                AND EXISTS(SELECT 1 FROM user_country_access b WHERE b.user_id=? AND b.country_id=co.country_id)""",(u["id"],u["id"])).fetchone()[0]
            granted={r["field_name"] for r in con.execute("SELECT field_name FROM user_field_access WHERE user_id=?",(u["id"],))}
            row["sales"]=[label for key,label in sales_fields.items() if key in granted]
            row["no_access"]=u["status"]=="ACTIVE" and row["visible"]==0
        users.append(row)
    by_role={}
    for r in users: by_role[r["role"]]=by_role.get(r["role"],0)+1
    return {"users":users,"by_role":by_role,"active":sum(r["status"]=="ACTIVE" for r in users),
            "disabled":sum(r["status"]!="ACTIVE" for r in users),"no_access":[r for r in users if r.get("no_access")],"sales_total":len(sales_fields)}

def parse_utc(text):
    try: return datetime.fromisoformat(str(text).rstrip("Z"))
    except (TypeError,ValueError): return None

def import_health(con,data_dir):
    """What is loaded per country, the latest import per country, and data/ workbooks that would
    replace newer data if imported (the import buttons replace a country's whole dataset)."""
    countries=[]
    for r in con.execute("""SELECT cn.name,cn.source_file,cn.updated_at,COUNT(co.id) companies FROM countries cn
        LEFT JOIN companies co ON co.country_id=cn.id GROUP BY cn.id ORDER BY cn.name""").fetchall():
        last=con.execute("SELECT status,records_imported,imported_at FROM imports WHERE country=? COLLATE NOCASE ORDER BY id DESC LIMIT 1",(r["name"],)).fetchone()
        countries.append({"name":r["name"],"source":re.sub(r"^[0-9a-f]{12}_","",r["source_file"] or "") or None,
                          "uploaded":bool(re.match(r"^[0-9a-f]{12}_",r["source_file"] or "")),"loaded":r["updated_at"],
                          "companies":r["companies"],"last":dict(last) if last else None})
    by_name={c["name"].lower():c for c in countries}
    files=[]
    for path in sorted(Path(data_dir).glob("*.xlsx")):
        country=detect_country(path.name); live=by_name.get(country.lower())
        modified=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).replace(tzinfo=None)
        loaded=parse_utc(live["loaded"]) if live else None
        state="new" if not live else "stale" if live["uploaded"] and loaded and modified<loaded else "ok"
        files.append({"name":path.name,"country":country,"modified":modified.isoformat(timespec="seconds")+"Z","state":state})
    failed=con.execute("SELECT COUNT(*) FROM imports WHERE status='FAILED'").fetchone()[0]
    return {"countries":countries,"files":files,"stale":[f for f in files if f["state"]=="stale"],"failed":failed}

LOG_LINE=re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (INFO|WARNING) (.*)$")
SIGNIN=re.compile(r"^(Failed|Throttled) login username=(.*?)(?: ip=(\S+))?$")

def activity(con,log_path,limit=15,tail_bytes=512*1024,now=None):
    """Latest admin actions and last-24h sign-in failures, read from the tail of app.log.
    Typed usernames are only shown when they match an account (people sometimes type a password there)."""
    path=Path(log_path)
    if not path.exists(): return {"actions":[],"failed":0,"throttled":0,"targets":[],"missing":True}
    with path.open("rb") as f:
        # Split on "\n" only (not str.splitlines), so other line-break characters cannot start a line.
        f.seek(max(0,path.stat().st_size-tail_bytes)); lines=f.read().decode("utf-8","replace").split("\n")
    known={r["username"].lower() for r in con.execute("SELECT username FROM users")}
    since=(now or datetime.now())-timedelta(hours=24)
    actions=[]; failed=0; throttled=0; targets={}
    for line in lines:
        m=LOG_LINE.match(line)
        if not m: continue
        when,msg=m.group(1),m.group(3).rstrip("\r")
        if msg.startswith("Admin "): actions.append({"when":when,"text":msg}); continue
        s=SIGNIN.match(msg)
        if s and datetime.strptime(when,"%Y-%m-%d %H:%M:%S")>=since:
            if s.group(1)=="Failed": failed+=1
            else: throttled+=1
            name=s.group(2).strip().lower() if s.group(2).strip().lower() in known else "unknown username"
            targets[name]=targets.get(name,0)+1
    return {"actions":actions[-limit:][::-1],"failed":failed,"throttled":throttled,"missing":False,
            "targets":sorted(targets.items(),key=lambda kv:(-kv[1],kv[0]))}

FORMULA_START=("=","+","-","@","\t","\r")
def csv_cell(v):
    """Plain text for a CSV cell; a leading formula character is neutralized so spreadsheets
    show the value instead of evaluating it (CSV injection)."""
    if v is None: return ""
    s=str(v)
    return "'"+s if s.startswith(FORMULA_START) else s

def export_rows(con,kind):
    """(header, rows) for the admin CSV export. companies: core columns plus every other XLSX column."""
    if kind=="contacts":
        header=["Contact ID","Company ID","Company","Country","Name","Job Position","Contact Type","Email","Phone","Landline No","Address"]
        rows=con.execute("""SELECT ct.id,co.id,co.company_name,cn.name,ct.name,ct.job_position,ct.contact_type,ct.email,ct.phone,ct.landline_no,ct.address
            FROM contacts ct JOIN companies co ON co.id=ct.company_id JOIN countries cn ON cn.id=co.country_id
            ORDER BY cn.name,co.company_name COLLATE NOCASE,ct.id""").fetchall()
        return header,[[csv_cell(v) for v in r] for r in rows]
    companies=con.execute("""SELECT co.id,cn.name country,co.company_name,co.city,co.state,co.network,co.address,co.source_data,
        (SELECT COUNT(*) FROM contacts ct WHERE ct.company_id=co.id) contacts
        FROM companies co JOIN countries cn ON cn.id=co.country_id ORDER BY cn.name,co.company_name COLLATE NOCASE""").fetchall()
    extra=[]
    for r in companies:
        for key,h in columns(list(load(r["source_data"]))):
            if key.startswith("x_") and h not in extra: extra.append(h)
    header=["Company ID","Country","Company","City","State","Network","Address","Contacts"]+extra
    rows=[]
    for r in companies:
        src=load(r["source_data"])
        rows.append([csv_cell(v) for v in [r["id"],r["country"],r["company_name"],r["city"],r["state"],r["network"],r["address"],r["contacts"]]+[src.get(h) for h in extra]])
    return header,rows
