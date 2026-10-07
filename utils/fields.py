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

SECTIONS=[("general","General information"),("contact","Contact information"),("payment","Payment term"),("additional","Additional information")]
# Columns describing the person on a row; everything else describes the company.
CONTACT_FIELDS=("name","contact_type","job_position","email","phone","landline_no","address")
# Order of the contact rows on the company page (Name is placed by the template, after Contact Type).
CONTACT_ORDER=["contact_type","name","job_position","email","phone","landline_no","address","x_sourceofagent","x_notes"]
# Per-contact columns of the indiav2 layout: shown after the street address even when every contact leaves them blank.
CONTACT_EXTRA=[("x_sourceofagent","Source of Agent"),("x_notes","Notes")]
MAPPED_SECTIONS={"company_name":"general","country":"general","state":"general","city":"general","address":"contact","network":"payment"}
# Unmapped XLSX columns keyed by norm(header).
EXTRA_SECTIONS={"agentidfinal":"general","alias":"general","agentstatus":"general","sourceofagent":"general",
                "kgsales":"contact","pcsales":"contact","tisales":"contact","kwsales":"contact",
                "paymentterms":"payment","networkexpiry":"payment","kycstatus":"payment","mappingstatus":"payment"}
# Cards within a section, in display order; fields not listed follow in workbook order under "Other details".
SECTION_GROUPS={"general":[("Agent",["x_agentidfinal","x_alias","company_name"]),("Location",["country","state","city"]),
                           ("Status",["x_agentstatus","x_sourceofagent"])],
                "contact":[("Sales team",["x_kgsales","x_pcsales","x_tisales","x_kwsales"]),("Company address",["address"])],
                "payment":[("Payment",["x_paymentterms","network","x_networkexpiry","city_state","x_kycstatus","x_mappingstatus"])]}
PAYMENT_FIELDS=[("x_paymentterms","Payment Terms"),("network","Network"),("x_networkexpiry","Network Expiry"),
                ("city_state","City and state"),("x_kycstatus","KYC Status"),("x_mappingstatus","Mapping Status")]
EXPIRY_SOON_DAYS=90  # expiry within this many days is flagged (company page, results, admin overview)
# "Street" (and other address aliases) is presented as Street address; the XLSX header is kept as a hint.
LABELS={"address":"Street address",
        # Sales display names (same as app.SALES_FIELDS); XLSX headers stay "KG Sales" etc.
        "x_kgsales":"Kargosmart Sales","x_pcsales":"Panda Cargo Sales","x_tisales":"Tri-Star Logistics Sales","x_kwsales":"KirinWorld Sales"}
# Headers assumed for records imported before source_data was captured.
LEGACY_HEADERS=["Company Name Entity","Country","State","City","Network","Contact Type","Name","Job Position","Email","Phone","Landline No","Address"]

EMPTY={"","none","null","nan","nat"}
EMAIL=re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")
URL=re.compile(r"^(https?://|www\.)\S+$",re.I)
DATE_FORMATS=("%d-%b-%Y","%d-%B-%Y","%b %d, %Y","%B %d, %Y","%d %b %Y","%Y-%m-%d","%d/%m/%Y")
TONES={"good":{"active","approved","verified","completed","done","yes","mapped"},
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
            out={"text":f"{d.day} {d:%b %Y}","raw":text}
            if kind=="expiry":
                days=(d-date.today()).days
                if days<0: out.update(note="Expired",note_tone="bad")
                elif days<=EXPIRY_SOON_DAYS: out.update(note=f"Expires in {days} day{'s' if days!=1 else ''}",note_tone="warn")
                else: out.update(note="Valid",note_tone="good")
            return out
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
    # Notes describe the contact on its row, so they are shown in the contacts table only.
    company_cols=[(k,h) for k,h in cols if (k not in CONTACT_FIELDS or k=="address") and k!="x_notes"]
    contact_cols=[(k,h) for k,h in cols if k in CONTACT_FIELDS and k!="name"]
    heads=dict(cols)
    contact_cols+=[(k,heads.get(k,h)) for k,h in CONTACT_EXTRA if can_view(k)]
    def company_value(k,h): return source.get(h) if k.startswith("x_") else company[k]
    sections=[]
    for sid,title in SECTIONS:
        fields=[field(k,h,company_value(k,h)) for k,h in company_cols if section_for(k)==sid]
        if sid=="payment":
            fields=[field(k,label,None) for k,label in PAYMENT_FIELDS
                    if ((can_view("city") or can_view("state")) if k=="city_state" else can_view(k))]
        groups=[]; placed=set()
        for gtitle,keys in SECTION_GROUPS.get(sid,[]):
            got=[f for k in keys for f in fields if f["key"]==k]
            if got: groups.append({"title":gtitle,"fields":got}); placed.update(f["key"] for f in got)
        rest=[f for f in fields if f["key"] not in placed]
        if rest: groups.append({"title":"Other details" if groups else "Details","fields":rest})
        fields=[f for g in groups for f in g["fields"]]
        # The contact sheet also holds the contact cards, so it is kept even without company fields.
        if fields or sid=="contact": sections.append({"id":sid,"title":title,"fields":fields,"groups":groups,"filled":sum(not f["value"].get("empty") for f in fields)})
    by_key={f["key"]:f for s in sections for f in s["fields"]}
    header={k:by_key[k] for k in ("x_agentidfinal","x_alias","x_agentstatus","network") if k in by_key and not by_key[k]["value"].get("empty")}
    if can_view("network") and not is_empty(company["network"]):
        header["network"]=field("network",heads.get("network","Network"),company["network"])
    payment_section=next((s for s in sections if s["id"]=="payment"),None)
    if payment_section is not None: payment_section["rows"]=[]
    cards=[]
    for n,ct in enumerate(contacts,start=1):
        row=load(ct["source_data"]); fields=[]
        if payment_section is not None:
            # Each contact retains its own workbook row. Never fill a missing
            # Payment cell from the company's first row.
            row_heads=dict(columns(list(row)))
            def row_value(key):
                if not key.startswith("x_") and key in ct.keys(): return ct[key]
                return row.get(row_heads.get(key))
            payment_fields=[]
            for f in payment_section["fields"]:
                key=f["key"]
                if key=="city_state":
                    parts=[row_value(k) for k in ("city","state") if can_view(k)]
                    val=", ".join(str(v).strip() for v in parts if not is_empty(v))
                else: val=row_value(key)
                payment_fields.append(field(key,f["label"],val))
            payment_section["rows"].append({"id":ct["id"],"fields":payment_fields,
                                          "empty":all(f["value"].get("empty") for f in payment_fields)})
        for k,h in contact_cols:
            # Source of Agent falls back to the company value; Notes belong to this row only.
            if k=="x_sourceofagent": fields.append(field(k,h,row.get(h) if not is_empty(row.get(h)) else source.get(h)))
            elif k.startswith("x_"): fields.append(field(k,h,row.get(h)))
            else: fields.append(field(k,h,ct[k]))
        name=ct["name"] if can_view("name") and not is_empty(ct["name"]) else None
        search=" ".join(str(ct[k]) for k in CONTACT_FIELDS if can_view(k) and not is_empty(ct[k])).lower()
        cards.append({"id":ct["id"],"name":name or f"Contact {n}","named":bool(name),"initials":initials(name) if name else "#",
                      "fields":fields,"by_key":{f["key"]:f for f in fields},"source_row":ct["source_row"],"search":search})
    # Contact rows: only those the user may see and at least one contact fills in (or CONTACT_EXTRA), in CONTACT_ORDER.
    always={k for k,h in CONTACT_EXTRA}
    contact_columns=[(k,LABELS.get(k,h)) for k,h in contact_cols if k in always or any(not c["by_key"][k]["value"].get("empty") for c in cards)]
    contact_columns.sort(key=lambda kh: CONTACT_ORDER.index(kh[0]) if kh[0] in CONTACT_ORDER else len(CONTACT_ORDER))
    return {"sections":sections,"header":header,"contacts":cards,"contact_columns":contact_columns,"has_source":bool(source),"by_key":by_key}
