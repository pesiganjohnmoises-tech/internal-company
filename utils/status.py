"""Operational status of an agent: agent status, KYC and network expiry, read from the stored XLSX row.

Search results, the company header and the admin overview all judge status here, so a company that
"needs attention" on the overview shows the same warning wherever users meet it.
"""
from datetime import date
from utils.fields import load, is_empty, parse_date, tone
from utils.importer import norm

EXPIRY_SOON_DAYS=90
RANK={"bad":3,"warn":2,"good":1,"neutral":0}

def agent_status(source_data,today=None):
    """{"tone": worst tone, "flags": [{"text","tone"}], "expiry": date or None} for one company."""
    today=today or date.today()
    row={norm(k):v for k,v in load(source_data).items()}
    flags=[]
    status=row.get("agentstatus")
    if not is_empty(status):
        label=str(status).strip(); flags.append({"text":label,"tone":tone(label),"kind":"agent"})
    kyc=row.get("kycstatus")
    if not is_empty(kyc):
        label=str(kyc).strip(); t=tone(label)
        # A completed KYC is the normal case; only an unfinished or failed one is worth a label.
        if t!="good": flags.append({"text":f"KYC {label}","tone":t,"kind":"kyc"})
    expiry=None; raw=row.get("networkexpiry")
    if not is_empty(raw):
        expiry=parse_date(raw)
        if expiry and expiry<today: flags.append({"text":f"Network expired {expiry.day} {expiry:%b %Y}","tone":"bad","kind":"expiry"})
        elif expiry and (expiry-today).days<=EXPIRY_SOON_DAYS:
            days=(expiry-today).days
            flags.append({"text":f"Network expires in {days} day{'s' if days!=1 else ''}","tone":"warn","kind":"expiry"})
    worst=max((i["tone"] for i in flags),key=lambda t:RANK.get(t,0),default="neutral")
    return {"tone":worst,"flags":flags,"expiry":expiry}

# ISO codes for the country tag on results; unknown names fall back to their first three letters.
CODES={"india":"IN","singapore":"SG","thailand":"TH","usa":"US","united states":"US","united states of america":"US",
       "malaysia":"MY","indonesia":"ID","vietnam":"VN","viet nam":"VN","philippines":"PH","china":"CN","hong kong":"HK",
       "taiwan":"TW","japan":"JP","korea":"KR","south korea":"KR","australia":"AU","new zealand":"NZ","bangladesh":"BD",
       "sri lanka":"LK","pakistan":"PK","cambodia":"KH","myanmar":"MM","united arab emirates":"AE","uae":"AE",
       "saudi arabia":"SA","qatar":"QA","oman":"OM","turkey":"TR","germany":"DE","netherlands":"NL","belgium":"BE",
       "france":"FR","united kingdom":"GB","uk":"GB","spain":"ES","italy":"IT","poland":"PL","canada":"CA","mexico":"MX",
       "brazil":"BR","south africa":"ZA","kenya":"KE","egypt":"EG","nigeria":"NG"}
def country_code(name):
    key=str(name or "").strip().lower()
    return CODES.get(key) or key[:3].upper() or "—"
