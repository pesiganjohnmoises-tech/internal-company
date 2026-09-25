"""Field model for the company detail page.

Sections are built from the XLSX columns captured at import (companies/contacts.source_data),
in workbook order. Columns the importer maps to database columns (importer.ALIASES) keep that
mapping and read the database value; every other column is shown under its original header.
A new XLSX column appears automatically, under "Additional information" unless it is listed
in EXTRA_SECTIONS.
"""
import re, json
from datetime import datetime, date
from utils.importer import column_map, norm

SECTIONS=[("company","Company information"),("location","Location"),("commercial","Network & commercial"),("additional","Additional information")]
# Columns describing the person on a row; everything else describes the company.
CONTACT_FIELDS=("name","contact_type","job_position","email","phone","landline_no","address")
MAPPED_SECTIONS={"company_name":"company","country":"location","state":"location","city":"location","address":"location","network":"commercial"}
# Unmapped XLSX columns keyed by norm(header).
EXTRA_SECTIONS={"agentidfinal":"company","alias":"company","agentstatus":"company","kycstatus":"company","mappingstatus":"company","sourceofagent":"company",
                "paymentterms":"commercial","networkexpiry":"commercial","kgsales":"commercial","pcsales":"commercial","tisales":"commercial","kwsales":"commercial"}
# "Street" (and other address aliases) is presented as Address; the XLSX header is kept as a hint.
LABELS={"address":"Address",
        # Sales display names (same as app.SALES_FIELDS); XLSX headers stay "KG Sales" etc.
        "x_kgsales":"Kargosmart Sales","x_pcsales":"Panda Cargo Sales","x_tisales":"Tri-Star Logistics Sales","x_kwsales":"KirinWorld Sales"}
# Headers assumed for records imported before source_data was captured.
LEGACY_HEADERS=["Company Name Entity","Country","State","City","Network","Contact Type","Name","Job Position","Email","Phone","Landline No","Address"]

EMPTY={"","none","null","nan","nat"}
EMAIL=re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")
URL=re.compile(r"^(https?://|www\.)\S+$",re.I)
DATE_FORMATS=("%d-%b-%Y","%d-%B-%Y","%b %d, %Y","%B %d, %Y","%d %b %Y","%Y-%m-%d","%d/%m/%Y")
TONES={"good":{"active","approved","verified","completed","yes","mapped"},
       "warn":{"pending","in progress","under review","on hold"},
       "bad":{"inactive","rejected","suspended","blocked","terminated","expired"}}

def load(raw):
    try: data=json.loads(raw) if raw else {}
    except ValueError: return {}
    return data if isinstance(data,dict) else {}
def is_empty(v):
    return v is None or (isinstance(v,float) and v!=v) or str(v).strip().lower() in EMPTY
def same(a,b):
    return ("" if is_empty(a) else str(a).strip().casefold())==("" if is_empty(b) else str(b).strip().casefold())
def columns(headers):
    """[(key, header)] in workbook order. Mapped headers use the importer's field name as key."""
    by_index={i:f for f,i in column_map(headers).items()}
    return [(by_index.get(i) or "x_"+norm(h),h) for i,h in enumerate(headers)]
def section_for(key):
    if key.startswith("x_"): return EXTRA_SECTIONS.get(key[2:],"additional")
    return MAPPED_SECTIONS.get(key,"additional")
def kind_for(key,header):
    if key=="email": return "email"
    if key in ("phone","landline_no"): return "phone"
    if key=="address": return "address"
    n=norm(header)
    if n.endswith("status"): return "status"
    if "expiry" in n: return "expiry"
    if "date" in n: return "date"
    return "text"
def parse_date(v):
    if isinstance(v,datetime): return v.date()
    if isinstance(v,date): return v
    s=str(v).strip()
    try: return datetime.fromisoformat(s).date()
    except ValueError: pass
    for fmt in DATE_FORMATS:
        try: return datetime.strptime(s,fmt).date()
        except ValueError: continue
    return None
def tone(label):
    low=label.strip().lower()
    return next((t for t,words in TONES.items() if low in words),"neutral")
def present(value,kind):
    """Display form of a stored value; the value itself is never modified."""
    if is_empty(value): return {"empty":True}
    if isinstance(value,bool): return {"text":"Yes" if value else "No"}
    text=str(value).strip()
    if kind=="email":
        found=EMAIL.findall(text)
        if found: return {"links":[(e,"mailto:"+e) for e in found]}
    if kind=="phone":
        parts=[p.strip() for p in re.split(r"[;,\n]",text) if p.strip()]
        return {"links":[(p,"tel:"+re.sub(r"[^\d+]","",p) if len(re.sub(r"\D","",p))>=6 else None) for p in parts]}
    if kind in ("date","expiry"):
        d=parse_date(value)
        if d:
            expired=kind=="expiry" and d<date.today()
            return {"text":f"{d.day} {d:%b %Y}","raw":text,"note":"Expired" if expired else None}
    if kind=="status":
        label={"y":"Yes","n":"No"}.get(text.lower(),text)
        return {"text":label,"tone":tone(label)}
    if isinstance(value,float): text=str(int(value)) if value.is_integer() else f"{value:,.2f}"
    if URL.match(text): return {"links":[(text,text if text.lower().startswith("http") else "https://"+text)],"external":True}
    return {"text":text}
def field(key,header,value):
    kind=kind_for(key,header); label=LABELS.get(key,header)
    return {"key":key,"label":label,"source":header if label!=header else None,"kind":kind,"value":present(value,kind)}
def initials(name):
    words=[w for w in re.split(r"\s+",str(name or "")) if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "?"

def build_company_detail(company,contacts,can_view):
    """Sections, header facts and contact cards for one company.
    can_view(key) applies the existing per-user field permissions."""
    source=load(company["source_data"])
    cols=[(k,h) for k,h in columns(list(source) or LEGACY_HEADERS) if can_view(k)]
    company_cols=[(k,h) for k,h in cols if k not in CONTACT_FIELDS or k=="address"]
    contact_cols=[(k,h) for k,h in cols if k in CONTACT_FIELDS and k!="name"]
    def company_value(k,h): return source.get(h) if k.startswith("x_") else company[k]
    sections=[]
    for sid,title in SECTIONS:
        fields=[field(k,h,company_value(k,h)) for k,h in company_cols if section_for(k)==sid]
        if fields: sections.append({"id":sid,"title":title,"fields":fields,"filled":sum(not f["value"].get("empty") for f in fields)})
    by_key={f["key"]:f for s in sections for f in s["fields"]}
    header={k:by_key[k] for k in ("x_agentidfinal","x_alias","x_agentstatus","network") if k in by_key and not by_key[k]["value"].get("empty")}
    cards=[]
    for n,ct in enumerate(contacts,start=1):
        row=load(ct["source_data"]); fields=[]
        for k,h in contact_cols:
            f=field(k,h,ct[k])
            # Contact rows usually repeat the company address; say so instead of repeating it.
            if k=="address" and not is_empty(company["address"]) and same(ct["address"],company["address"]):
                f["value"]={"text":"Same as company address","same":True}
            fields.append(f)
        # Company-level columns filled on this source row with a value other than the company record's.
        # Blank cells are not differences: sheets often fill company columns on the first row only.
        differs=[field(k,h,row.get(h)) for k,h in company_cols if k!="address" and source and not is_empty(row.get(h)) and not same(row.get(h),source.get(h))]
        name=ct["name"] if can_view("name") and not is_empty(ct["name"]) else None
        search=" ".join(str(ct[k]) for k in CONTACT_FIELDS if can_view(k) and not is_empty(ct[k])).lower()
        cards.append({"id":ct["id"],"name":name or f"Contact {n}","named":bool(name),"initials":initials(name) if name else "#",
                      "fields":fields,"by_key":{f["key"]:f for f in fields},"differs":differs,"source_row":ct["source_row"],"search":search})
    # Contact table columns: only those the user may see and at least one contact fills in.
    contact_columns=[(k,LABELS.get(k,h)) for k,h in contact_cols if any(not c["by_key"][k]["value"].get("empty") and not c["by_key"][k]["value"].get("same") for c in cards)]
    return {"sections":sections,"header":header,"contacts":cards,"contact_columns":contact_columns,"has_source":bool(source),"by_key":by_key}
