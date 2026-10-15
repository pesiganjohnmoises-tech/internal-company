"""Administrator previews on private, country-only disposable databases. No live import."""
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from utils import importer, workbook_submissions as submissions
from utils.backup_lock import file_lock

REVIEWABLE=('PENDING_REVIEW','NEEDS_REVIEW','IMPORT_FAILED')

class ReviewConflict(ValueError): pass

def _admin(con,actor_id):
    row=con.execute('SELECT role,status FROM users WHERE id=?',(actor_id,)).fetchone()
    if not row or row['role']!='ADMIN' or row['status']!='ACTIVE': raise PermissionError('Administrator access required.')

def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()

def _key(name):
    # SQLite lower(trim()) lowercases ASCII only; Python casefold would change matching.
    return name.strip(' ').translate(str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'))

def _values(row,company=False):
    fields=('company_name','agent_id','network','contact_type','address','city','state') if company else (
        'name','job_position','email','phone','address','contact_type','landline_no')
    result={key:row.get(key) for key in fields}
    try: raw=json.loads(row.get('source_data') or '{}')
    except (ValueError,TypeError): raw={'Unparsed source data':row.get('source_data')}
    if isinstance(raw,dict): result.update({'Workbook: '+key:value for key,value in raw.items()})
    return result

def _changes(before,after):
    return [{'field':key,'before':before.get(key),'after':after.get(key)}
            for key in sorted(before.keys()|after.keys()) if before.get(key)!=after.get(key)]

def _contacts(before,after,company,entries,counts):
    old=defaultdict(list); new=defaultdict(list)
    for row in before: old[_hash(_values(row))].append(row)
    for row in after: new[_hash(_values(row))].append(row)
    left=[]; right=[]
    for key in sorted(old.keys()|new.keys()):
        unchanged=min(len(old[key]),len(new[key])); counts['unchanged_contacts']+=unchanged
        left.extend(old[key][unchanged:]); right.extend(new[key][unchanged:])
    # Pair unique, identical nonempty names/types only. This is an explicit logical matching
    # convention, never a database-ID comparison; ambiguous groups remain unresolved.
    identity=lambda r: (r.get('name'),r.get('contact_type')) if r.get('name') else None
    olds=defaultdict(list); news=defaultdict(list)
    for row in left: olds[identity(row)].append(row)
    for row in right: news[identity(row)].append(row)
    for key in sorted(olds.keys()|news.keys(),key=repr):
        a=olds[key]; b=news[key]
        if key is not None and len(a)==len(b)==1:
            counts['updated_contacts']+=1
            entries.append({'kind':'Updated contact','company':company,'label':a[0]['name'],
                'source_row':b[0]['source_row'],'fields':_changes(_values(a[0]),_values(b[0]))})
            left.remove(a[0]); right.remove(b[0])
    if left and right:
        counts['ambiguous_contacts']+=len(left)+len(right)
        for rows,side in ((left,'Current'),(right,'Incoming')):
            for row in rows:
                entries.append({'kind':'Ambiguous contacts','company':company,
                    'label':f'{side} unmatched row: {row.get("name") or "(unnamed contact)"}. '
                            f'{len(left)} current and {len(right)} incoming rows need manual comparison.',
                    'source_row':row['source_row'] if side=='Incoming' else None,
                    'fields':_changes(_values(row),{}) if side=='Current' else _changes({},_values(row))})
    else:
        for rows,kind,counter in ((left,'Removed contact','removed_contacts'),(right,'Added contact','added_contacts')):
            for row in rows:
                counts[counter]+=1
                entries.append({'kind':kind,'company':company,'label':row.get('name') or '(unnamed contact)',
                    'source_row':row['source_row'],'source_side':'Current' if left else 'Incoming',
                    'fields':_changes(_values(row) if left else {},{} if left else _values(row))})

def snapshot(con,row):
    """Read the exact baseline from the caller's transaction, including access impact."""
    country=con.execute('SELECT * FROM countries WHERE id=?',(row['country_id'],)).fetchone()
    if not country or country['name']!=row['country_name']:
        raise ReviewConflict('The selected country has changed or was removed. Request a new submission.')
    country=dict(country)
    companies=[dict(r) for r in con.execute('SELECT * FROM companies WHERE country_id=? ORDER BY id',(country['id'],))]
    contacts=[dict(r) for r in con.execute('SELECT ct.* FROM contacts ct JOIN companies co ON co.id=ct.company_id WHERE co.country_id=? ORDER BY ct.id',(country['id'],))]
    grants=[dict(r) for r in con.execute('''SELECT a.user_id,a.company_id,u.username FROM user_company_access a
            JOIN companies co ON co.id=a.company_id JOIN users u ON u.id=a.user_id WHERE co.country_id=? ORDER BY a.user_id,a.company_id''',(country['id'],))]
    country_grants=[dict(r) for r in con.execute('SELECT * FROM user_country_access WHERE country_id=? ORDER BY user_id',(country['id'],))]
    baseline=_hash({'country':country,'companies':companies,'contacts':contacts,'grants':grants,
                    'country_grants':country_grants,'workbook':row['sha256'],
                    'rules':hashlib.sha256(Path(importer.__file__).read_bytes()+Path(__file__).read_bytes()+Path(submissions.__file__).read_bytes()).hexdigest()})
    return baseline,country,companies,contacts,grants

def preview(project,actor_id,submission_id):
    """Snapshot one country, simulate the real importer there, and compare logical values."""
    with closing(submissions._connect(project)) as con:
        con.execute('BEGIN'); _admin(con,actor_id)
        record=con.execute('SELECT * FROM workbook_submissions WHERE id=?',(submission_id,)).fetchone()
        if not record: raise LookupError('Submission not found.')
        row=dict(record)
        if row['status'] not in REVIEWABLE: raise ReviewConflict('This submission is not awaiting review.')
        baseline,country,companies,contacts,grants=snapshot(con,row)
        schemas={table:con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0]
                 for table in ('countries','companies','contacts')}
    with tempfile.TemporaryDirectory(prefix='directory-workbook-review-') as folder:
        folder=Path(folder); workbook=folder/'preview.xlsx'; database=folder/'preview.db'
        with submissions.verified_workbook(project,actor_id,submission_id) as incoming:
            with workbook.open('xb') as out:
                for chunk in iter(lambda:incoming.read(1024*1024),b''): out.write(chunk)
        validation=submissions._validate(workbook,country['name'],country['code'])
        # The snapshot has no accounts, password hashes, MFA material, or unrelated countries.
        with closing(sqlite3.connect(database)) as target,target:
            for sql in schemas.values(): target.execute(sql)
            for table,records in (('countries',[country]),('companies',companies),('contacts',contacts)):
                for record in records:
                    target.execute('INSERT INTO '+table+'('+','.join(record)+') VALUES('+','.join('?' for _ in record)+')',tuple(record.values()))
        imported=importer.import_workbook(workbook,database,country['name'],replace_country=True)
        if imported['imported']!=validation['rows']:
            raise ReviewConflict('Workbook dimensions or import interpretation disagree with validation. Resave the workbook and submit a new copy.')
        with closing(sqlite3.connect(database)) as target:
            target.row_factory=sqlite3.Row
            after=[dict(r) for r in target.execute('SELECT * FROM companies ORDER BY id')]
            after_contacts=[dict(r) for r in target.execute('SELECT * FROM contacts ORDER BY id')]
    entries=[]; concerns=[]
    counts={key:0 for key in ('added_companies','removed_companies','updated_companies','unchanged_companies',
        'added_contacts','removed_contacts','updated_contacts','unchanged_contacts','ambiguous_contacts','affected_grants')}
    old=defaultdict(list); new={_key(r['company_name']):r for r in after}
    for company in companies: old[_key(company['company_name'])].append(company)
    old_contacts=defaultdict(list); new_contacts=defaultdict(list)
    for contact in contacts: old_contacts[contact['company_id']].append(contact)
    for contact in after_contacts: new_contacts[contact['company_id']].append(contact)
    for key in sorted(old.keys()|new.keys()):
        originals=old[key]; incoming=new.get(key)
        retained=next((r for r in originals if incoming and r['id']==incoming['id']),None)
        if len(originals)>1: concerns.append('Multiple existing companies share the same trimmed matching name: '+originals[0]['company_name'])
        for original in originals:
            if original is retained: continue
            counts['removed_companies']+=1
            entries.append({'kind':'Removed company','company':original['company_name'],'label':'Missing from replacement or collapsed duplicate matching name.',
                            'fields':_changes(_values(original,True),{})})
            _contacts(old_contacts[original['id']],[],original['company_name'],entries,counts)
            for grant in grants:
                if grant['company_id']==original['id']:
                    counts['affected_grants']+=1
                    entries.append({'kind':'Access impact','company':original['company_name'],
                        'label':'Company-specific grant removed for @'+grant['username'],'fields':[]})
        if not incoming: continue
        if retained:
            fields=_changes(_values(retained,True),_values(incoming,True))
            counts['updated_companies' if fields else 'unchanged_companies']+=1
            if fields: entries.append({'kind':'Updated company','company':incoming['company_name'],'label':'First workbook row supplies company fields.',
                                       'source_row':incoming['source_row'],'fields':fields})
            _contacts(old_contacts[retained['id']],new_contacts[incoming['id']],incoming['company_name'],entries,counts)
        else:
            counts['added_companies']+=1
            entries.append({'kind':'Added company','company':incoming['company_name'],'label':'New matching name.',
                            'source_row':incoming['source_row'],'fields':_changes({},_values(incoming,True))})
            _contacts([],new_contacts[incoming['id']],incoming['company_name'],entries,counts)
    if counts['ambiguous_contacts']: concerns.append('Ambiguous contact groups are excluded from added/removed/updated totals. Inspect their full current and incoming rows.')
    if counts['removed_companies']: concerns.append('Missing or renamed company names are treated as removals and additions; grants are not transferred to renamed companies.')
    if companies and counts['removed_companies']/len(companies)>=0.25: concerns.append('At least 25% of current companies would be removed. Confirm that this is a complete country workbook.')
    return {'submission':row,'baseline':baseline,'counts':counts,'entries':entries,'concerns':concerns,
            'validation':validation,'result_signature':result_signature(after,after_contacts)}

def result_signature(companies,contacts):
    """Business/source values and source rows, independent of regenerated contact/new-company IDs."""
    names={r['id']:r['company_name'] for r in companies}
    company_values=[(_values(r,True),r['source_row']) for r in companies]
    contact_values=[(names[r['company_id']],_values(r),r['source_row']) for r in contacts]
    return _hash({'companies':sorted(company_values,key=lambda r:json.dumps(r,sort_keys=True,default=str)),
                  'contacts':sorted(contact_values,key=lambda r:json.dumps(r,sort_keys=True,default=str))})

def record_review(project,actor_id,submission_id,baseline):
    result=preview(project,actor_id,submission_id)
    if result['baseline']!=baseline: raise ReviewConflict('Directory data or review rules changed. Inspect the refreshed preview before recording review.')
    # Approval rechecks this baseline under the import write lock. Recording review
    # alone never authorizes an import.
    with file_lock(Path(project)/'.backup-import.lock'),closing(submissions._connect(project)) as con,con:
        con.execute('BEGIN IMMEDIATE'); _admin(con,actor_id)
        changed=con.execute('''UPDATE workbook_submissions SET review_baseline=?,reviewer_id=?,reviewed_at=?,updated_at=?
            WHERE id=? AND status IN ('PENDING_REVIEW','NEEDS_REVIEW','IMPORT_FAILED')''',
            (baseline,actor_id,importer.utcnow(),importer.utcnow(),submission_id)).rowcount
        if not changed: raise ReviewConflict('Submission status changed. Reload the review queue.')
        submissions._event(con,submission_id,actor_id,'REVIEW_RECORDED')
    return result

def reject(project,actor_id,submission_id,reason):
    if not isinstance(reason,str) or not reason.strip() or len(reason)>2000:
        raise ValueError('Enter a rejection reason of 1–2,000 characters.')
    if not submissions.ID.fullmatch(submission_id): raise LookupError('Submission not found.')
    with file_lock(Path(project)/'.backup-import.lock'),closing(submissions._connect(project)) as con,con:
        con.execute('BEGIN IMMEDIATE'); _admin(con,actor_id)
        changed=con.execute('''UPDATE workbook_submissions SET status='REJECTED',rejection_reason=?,reviewer_id=?,
            reviewed_at=?,updated_at=? WHERE id=? AND status IN ('PENDING_REVIEW','NEEDS_REVIEW','IMPORT_FAILED')''',
            (reason.strip(),actor_id,importer.utcnow(),importer.utcnow(),submission_id)).rowcount
        if not changed: raise ReviewConflict('Submission is no longer awaiting review. Reload the queue.')
        submissions._event(con,submission_id,actor_id,'REJECTED')
