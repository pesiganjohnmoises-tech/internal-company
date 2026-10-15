"""Explicit, backed-up approval. Directory/status/history commit in one import transaction."""
from contextlib import closing
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile

from utils import importer, workbook_review as review, workbook_submissions as submissions
from utils.backup_lock import file_lock
from utils.upload_archive import preserve_upload

class ApprovalFailed(RuntimeError): pass
class ApprovalDenied(PermissionError): pass
class StaleReview(review.ReviewConflict): pass

def _record(con,actor_id,submission_id):
    try: review._admin(con,actor_id)
    except PermissionError: raise ApprovalDenied('Administrator access required.') from None
    row=con.execute('SELECT * FROM workbook_submissions WHERE id=?',(submission_id,)).fetchone()
    if not row: raise LookupError('Submission not found.')
    return dict(row)

def _duplicate(con,row):
    if con.execute("SELECT 1 FROM workbook_submissions WHERE country_id=? AND sha256=? AND status='IMPORTED' AND id!=?",
                   (row['country_id'],row['sha256'],row['id'])).fetchone():
        raise review.ReviewConflict('This exact workbook has already been imported for this country. It cannot be imported twice.')

def _saved_review(row,baseline):
    if row['status'] not in review.REVIEWABLE: raise review.ReviewConflict('Submission is not awaiting approval.')
    if not baseline or row['review_baseline']!=baseline:
        raise StaleReview('Record a fresh review of the current preview before approving this workbook.')

def _invalidate(project,actor_id,submission_id,status,feedback,backup=None):
    with closing(submissions._connect(project)) as con,con:
        changed=con.execute("""UPDATE workbook_submissions SET status=?,review_baseline=NULL,feedback=?,
            backup_reference=COALESCE(?,backup_reference),updated_at=? WHERE id=?
            AND status IN ('PENDING_REVIEW','NEEDS_REVIEW','IMPORT_FAILED')""",
            (status,feedback,backup,importer.utcnow(),submission_id)).rowcount
        if changed: submissions._event(con,submission_id,actor_id,status)

def recovery_backup(project,baseline,row):
    # Lazy import: normal page loads do not run scheduled-task setup or any backup.
    from scripts import scheduled_backup
    destination=submissions._safe_path(project/'backups'/'workbook-approvals')
    destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(destination,0o700)
    backup=scheduled_backup.create_backup(project,destination,datetime.now(timezone.utc),lock_held=True)
    scheduled_backup.verify(backup)
    # Confirm that the backed-up selected country is precisely the reviewed baseline,
    # even if some uncoordinated writer changed data while the archive was being made.
    with tempfile.TemporaryDirectory(prefix='approval-backup-check-') as folder:
        database=Path(folder)/'directory.db'
        with zipfile.ZipFile(backup) as archive,database.open('xb') as out:
            with archive.open('directory.db') as incoming:
                for chunk in iter(lambda:incoming.read(1024*1024),b''): out.write(chunk)
        with closing(sqlite3.connect(database)) as con:
            con.row_factory=sqlite3.Row
            if review.snapshot(con,row)[0]!=baseline:
                raise StaleReview('Directory data changed while backing up. Review the refreshed preview before approving.')
    return backup.relative_to(project).as_posix()

def approve(project,actor_id,submission_id,baseline,authorize,*,accept_ambiguous=False):
    """authorize(con) must recheck the requesting session/MFA under the import write lock.

    The coordination lock serializes all approval attempts. IMPORTING is never durably
    committed on its own: interruption rolls back to the reviewable state, while a
    successful import, history ID, backup reference and audit event commit together.
    """
    project=submissions._safe_path(project)
    if not submissions.ID.fullmatch(submission_id): raise LookupError('Submission not found.')
    with file_lock(project/'.backup-import.lock',timeout=5):
        with closing(submissions._connect(project)) as con:
            row=_record(con,actor_id,submission_id); authorize(con)
            if row['status']=='IMPORTED': return row
            _duplicate(con,row)
            _saved_review(row,baseline)
        backup=None
        try:
            proposed=review.preview(project,actor_id,submission_id)
            if proposed['baseline']!=baseline: raise StaleReview('The directory or import rules changed. Inspect and record the refreshed review.')
            if proposed['counts']['ambiguous_contacts'] and accept_ambiguous is not True:
                raise review.ReviewConflict('Explicitly confirm that you reviewed the ambiguous current/incoming contact rows before approving.')
            with tempfile.TemporaryDirectory(prefix='directory-approved-workbook-') as folder:
                workbook=Path(folder)/(submission_id+'.xlsx')
                with submissions.verified_workbook(project,actor_id,submission_id) as incoming,workbook.open('xb') as out:
                    for chunk in iter(lambda:incoming.read(1024*1024),b''): out.write(chunk)
                archived=submissions._safe_path(project/'upload_archive'/(submission_id+'.xlsx'))
                if archived.exists():
                    with archived.open('rb') as incoming:
                        if submissions._digest(incoming)!=(row['sha256'],row['size_bytes']): raise OSError('Archive integrity check failed.')
                else: preserve_upload(workbook,project/'upload_archive')
                backup=recovery_backup(project,baseline,row)
                with closing(submissions._connect(project)) as con,con:
                    con.execute('BEGIN IMMEDIATE')
                    current=_record(con,actor_id,submission_id); authorize(con); _saved_review(current,baseline)
                    con.execute('UPDATE workbook_submissions SET backup_reference=?,updated_at=? WHERE id=?',
                                (backup,importer.utcnow(),submission_id))
                    submissions._event(con,submission_id,actor_id,'APPROVAL_STARTED')

                def before_apply(con):
                    current=_record(con,actor_id,submission_id); authorize(con); _saved_review(current,baseline); _duplicate(con,current)
                    if review.snapshot(con,current)[0]!=baseline:
                        raise StaleReview('The directory changed before import. Review the refreshed preview before approving.')
                    # Check all copies again, including the bytes parsed by this import.
                    with submissions.verified_workbook(project,actor_id,submission_id): pass
                    for path in (workbook,archived):
                        with path.open('rb') as incoming:
                            if submissions._digest(incoming)!=(current['sha256'],current['size_bytes']):
                                raise OSError('Workbook changed before import.')
                    con.execute("UPDATE workbook_submissions SET status='IMPORTING' WHERE id=?",(submission_id,))

                def completed(con,result):
                    authorize(con)
                    companies=[dict(r) for r in con.execute('SELECT * FROM companies WHERE country_id=? ORDER BY id',(row['country_id'],))]
                    contacts=[dict(r) for r in con.execute('SELECT ct.* FROM contacts ct JOIN companies co ON co.id=ct.company_id WHERE co.country_id=? ORDER BY ct.id',(row['country_id'],))]
                    if review.result_signature(companies,contacts)!=proposed['result_signature'] or result['imported']!=proposed['validation']['rows']:
                        raise RuntimeError('Import result did not match preview.')
                    stamp=importer.utcnow()
                    import_id=con.execute('''INSERT INTO imports(source_file,country,status,records_processed,records_imported,
                        duplicates_found,errors_found,imported_at) VALUES(?,?,'COMPLETED',?,?,?,?,?)''',
                        (workbook.name,row['country_name'],result['processed'],result['imported'],result['duplicates'],len(result['errors']),stamp)).lastrowid
                    con.execute("""UPDATE workbook_submissions SET status='IMPORTED',import_id=?,backup_reference=?,
                        reviewer_id=?,reviewed_at=?,updated_at=?,feedback='' WHERE id=?""",
                        (import_id,backup,actor_id,stamp,stamp,submission_id))
                    if proposed['counts']['ambiguous_contacts']:
                        submissions._event(con,submission_id,actor_id,'AMBIGUOUS_ROWS_ACCEPTED')
                    submissions._event(con,submission_id,actor_id,'APPROVED_AND_IMPORTED')
                    others=[r[0] for r in con.execute("SELECT id FROM workbook_submissions WHERE country_id=? AND id!=? AND status IN ('PENDING_REVIEW','NEEDS_REVIEW','IMPORT_FAILED')",
                                                     (row['country_id'],submission_id))]
                    for other in others:
                        con.execute("UPDATE workbook_submissions SET status='NEEDS_REVIEW',review_baseline=NULL,feedback=?,updated_at=? WHERE id=?",
                                    ('The country dataset changed. An administrator must review the latest changes again.',stamp,other))
                        submissions._event(con,other,actor_id,'REVIEW_INVALIDATED')
                importer.import_workbook(workbook,project/'directory.db',row['country_name'],replace_country=True,
                                         before_apply=before_apply,on_success=completed)
        except StaleReview:
            _invalidate(project,actor_id,submission_id,'NEEDS_REVIEW','The preview changed. A fresh administrator review is required.',backup)
            raise
        except (ApprovalDenied,review.ReviewConflict): raise
        except Exception as error:
            # Never change a successfully committed result into a failure on response/retry.
            with closing(submissions._connect(project)) as con:
                current=con.execute('SELECT * FROM workbook_submissions WHERE id=?',(submission_id,)).fetchone()
                if current and current['status']=='IMPORTED': return dict(current)
            logging.error('Workbook approval failed submission_id=%s error_type=%s',submission_id,type(error).__name__)
            _invalidate(project,actor_id,submission_id,'IMPORT_FAILED',
                        'Import did not complete. Previous directory data was kept. An administrator must review again before retrying.',backup)
            raise ApprovalFailed('Import did not complete. Previous directory data was kept. Review again before retrying.') from None
        with closing(submissions._connect(project)) as con: return _record(con,actor_id,submission_id)
