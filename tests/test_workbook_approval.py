"""Approval transactions/backups/retries on isolated synthetic app copies only."""
from contextlib import closing
import concurrent.futures
import io
from pathlib import Path
import runpy
import shutil
import unittest
from unittest.mock import patch

SRC=Path(__file__).resolve().parent.parent
state=runpy.run_path(str(SRC/'tests'/'test_workbook_review.py'))
A=state['A']; csrf=state['csrf']
shutil.copytree(SRC/'scripts',A.ROOT/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
from scripts import scheduled_backup as backups

class WorkbookApprovalTests(unittest.TestCase):
    def setUp(self):
        self.fixture=state['WorkbookReviewTests'](); self.fixture.setUp()
        for key in ('admin','uid','cid','keep','remove','row','path','client','country'):
            setattr(self,key,getattr(self.fixture,key))
        self.baseline=self.save_review()
        self.authorize=lambda con:A.workbook_review._admin(con,self.admin)

    def save_review(self,row=None):
        row=row or self.row
        result=A.workbook_review.preview(A.ROOT,self.admin,row['id'])
        A.workbook_review.record_review(A.ROOT,self.admin,row['id'],result['baseline'])
        return result['baseline']

    def approve(self,**kwargs):
        return A.workbook_approval.approve(A.ROOT,self.admin,self.row['id'],self.baseline,self.authorize,**kwargs)

    def stored(self): return A.workbook_submissions.get_submission(A.ROOT,self.admin,self.row['id'])
    def post(self,**overrides):
        data={'csrf_token':csrf(self.client,self.path),'action':'approve','confirmed':'1',
              'baseline':self.baseline,'confirm_replacement':'1'}
        data.update(overrides); return self.client.post(self.path,data=data)

    def test_success_backup_restore_ids_grants_and_exactly_once(self):
        with closing(A.db()) as c,c:
            c.execute('INSERT INTO user_company_access VALUES(?,?)',(self.uid,self.keep))
        self.baseline=self.save_review()
        original=self.fixture.snapshot()
        result=self.approve(); self.assertEqual(result['status'],'IMPORTED'); self.assertTrue(result['import_id'])
        archive=A.ROOT/result['backup_reference']; manifest=backups.verify(archive)
        self.assertIn('workbook_submissions/'+self.row['id']+'.xlsx',manifest['files'])
        self.assertIn('upload_archive/'+self.row['id']+'.xlsx',manifest['files'])
        restored=A.ROOT/('restored-approval-'+self.row['id']); backups.restore(archive,restored)
        with closing(A.sqlite3.connect(restored/'directory.db')) as c:
            self.assertEqual(c.execute('SELECT status FROM workbook_submissions WHERE id=?',(self.row['id'],)).fetchone()[0],'PENDING_REVIEW')
            self.assertEqual(c.execute('SELECT email FROM contacts WHERE company_id=?',(self.keep,)).fetchone()[0],'old@example.test')
        with closing(A.db()) as c:
            self.assertEqual(c.execute("SELECT id FROM companies WHERE country_id=? AND company_name='Keep'",(self.cid,)).fetchone()[0],self.keep)
            self.assertIsNotNone(c.execute('SELECT 1 FROM user_company_access WHERE user_id=? AND company_id=?',(self.uid,self.keep)).fetchone())
            self.assertIsNone(c.execute("SELECT 1 FROM companies WHERE company_name='Remove' AND country_id=?",(self.cid,)).fetchone())
            self.assertEqual(c.execute('SELECT email FROM contacts WHERE company_id=?',(self.keep,)).fetchone()[0],'new@example.test')
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
            history=c.execute('SELECT * FROM imports WHERE id=?',(result['import_id'],)).fetchone()
            self.assertEqual(history['source_file'],self.row['id']+'.xlsx')
        after=self.fixture.snapshot(); repeated=self.approve()
        self.assertEqual(result['import_id'],repeated['import_id']); self.assertEqual(after,self.fixture.snapshot())
        self.assertNotEqual(original,after)

    def test_http_confirmation_cancel_csrf_and_missing_acknowledgment(self):
        before=self.fixture.snapshot()
        result=self.post(confirmed=''); self.assertEqual(result.status_code,200)
        self.assertIn('Confirm country replacement',result.get_data(as_text=True))
        self.assertEqual(self.client.get(self.path).status_code,200)
        self.assertEqual(before,self.fixture.snapshot())
        for fields in ({'csrf_token':''},{'csrf_token':'bad'},{'confirm_replacement':''}):
            self.assertEqual(self.post(**fields).status_code,400)
        self.assertEqual(self.post().status_code,302)
        self.assertEqual(self.stored()['status'],'IMPORTED')
        self.assertIn('Approved and imported',self.client.get(self.path).get_data(as_text=True))
        self.assertIn('Approved submissions list their recovery archive',self.client.get('/admin/imports').get_data(as_text=True))
        self.assertEqual(self.post().status_code,302)

    def test_stale_preview_and_late_write_rejected_without_overwriting_data(self):
        with closing(A.db()) as c,c: c.execute("UPDATE contacts SET phone='External change' WHERE company_id=?",(self.keep,))
        before=self.fixture.snapshot()
        with self.assertRaises(A.workbook_review.ReviewConflict): self.approve()
        self.assertEqual(self.stored()['status'],'NEEDS_REVIEW'); self.assertEqual(before,self.fixture.snapshot())
        self.baseline=self.save_review()
        actual=A.workbook_approval.recovery_backup
        def late_write(*args):
            result=actual(*args)
            with closing(A.db()) as c,c: c.execute("UPDATE contacts SET phone='Later external change' WHERE company_id=?",(self.keep,))
            return result
        with patch.object(A.workbook_approval,'recovery_backup',side_effect=late_write):
            with self.assertRaises(A.workbook_review.ReviewConflict): self.approve()
        with closing(A.db()) as c:
            self.assertEqual(c.execute('SELECT phone FROM contacts WHERE company_id=?',(self.keep,)).fetchone()[0],'Later external change')
            self.assertEqual(c.execute('SELECT email FROM contacts WHERE company_id=?',(self.keep,)).fetchone()[0],'old@example.test')

    def test_backup_failure_requires_fresh_review_and_retry(self):
        before=self.fixture.snapshot()
        with patch.object(A.workbook_approval,'recovery_backup',side_effect=OSError('Synthetic backup failure')):
            with self.assertRaises(A.workbook_approval.ApprovalFailed): self.approve()
        self.assertEqual(before,self.fixture.snapshot()); self.assertEqual(self.stored()['status'],'IMPORT_FAILED')
        with self.assertRaises(A.workbook_review.ReviewConflict): self.approve()
        self.baseline=self.save_review(); self.assertEqual(self.approve()['status'],'IMPORTED')

    def test_transaction_failure_rolls_back_records_history_and_status(self):
        before=self.fixture.snapshot(); actual=A.workbook_submissions._event
        def failed(con,submission,actor,event):
            if event=='APPROVED_AND_IMPORTED': raise A.sqlite3.OperationalError('Synthetic transaction failure')
            return actual(con,submission,actor,event)
        with patch.object(A.workbook_submissions,'_event',side_effect=failed):
            with self.assertRaises(A.workbook_approval.ApprovalFailed): self.approve()
        self.assertEqual(before,self.fixture.snapshot()); self.assertEqual(self.stored()['status'],'IMPORT_FAILED')
        self.assertIsNone(self.stored()['import_id']); self.assertTrue(self.stored()['backup_reference'])

    def test_interruption_rolls_back_and_explicit_retry_does_not_double_import(self):
        before=self.fixture.snapshot(); actual=A.workbook_submissions._event
        def interrupted(con,submission,actor,event):
            if event=='APPROVED_AND_IMPORTED': raise KeyboardInterrupt('Synthetic process interruption')
            return actual(con,submission,actor,event)
        with patch.object(A.workbook_submissions,'_event',side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt): self.approve()
        self.assertEqual(before,self.fixture.snapshot()); self.assertEqual(self.stored()['status'],'PENDING_REVIEW')
        self.assertIsNone(self.stored()['import_id'])
        self.assertEqual(self.approve()['status'],'IMPORTED')

    def test_concurrent_requests_and_other_pending_baselines(self):
        second=self.fixture.submit([['Keep','Alice','different@example.test','Changed']])
        self.save_review(second)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.approve(),range(2)))
        self.assertEqual(results[0]['import_id'],results[1]['import_id'])
        other=A.workbook_submissions.get_submission(A.ROOT,self.admin,second['id'])
        self.assertEqual(other['status'],'NEEDS_REVIEW'); self.assertIsNone(other['review_baseline'])
        with closing(A.db()) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM workbook_submission_events WHERE submission_id=? AND event='APPROVED_AND_IMPORTED'",(self.row['id'],)).fetchone()[0],1)

    def test_duplicate_original_tamper_and_archive_tamper(self):
        self.approve()
        with A.workbook_submissions.verified_workbook(A.ROOT,self.admin,self.row['id']) as stream: payload=stream.read()
        duplicate=A.workbook_submissions.create_submission(A.ROOT,self.uid,self.cid,'duplicate.xlsx',io.BytesIO(payload),complete_country=True)
        baseline=self.save_review(duplicate)
        with self.assertRaises(A.workbook_review.ReviewConflict):
            A.workbook_approval.approve(A.ROOT,self.admin,duplicate['id'],baseline,self.authorize)
        third=self.fixture.submit([['Keep','Alice','third@example.test','Third address']])
        baseline=self.save_review(third)
        original=A.ROOT/'workbook_submissions'/(third['id']+'.xlsx'); contents=original.read_bytes()
        try:
            original.write_bytes(b'Synthetic tamper')
            with self.assertRaises(A.workbook_approval.ApprovalFailed):
                A.workbook_approval.approve(A.ROOT,self.admin,third['id'],baseline,self.authorize)
        finally: original.write_bytes(contents)

    def test_session_revocation_during_backup_prevents_import(self):
        before=self.fixture.snapshot(); actual=A.workbook_approval.recovery_backup
        def revoke(*args):
            result=actual(*args)
            with closing(A.db()) as c,c: c.execute('DELETE FROM security_sessions WHERE user_id=?',(self.admin,))
            return result
        with patch.object(A.workbook_approval,'recovery_backup',side_effect=revoke):
            self.assertEqual(self.post().status_code,403)
        self.assertEqual(before,self.fixture.snapshot()); self.assertIsNone(self.stored()['import_id'])

    def test_ambiguous_contacts_need_explicit_confirmation(self):
        row=self.fixture.submit([['Keep','Renamed contact','different@example.test','Updated']])
        baseline=self.save_review(row)
        with self.assertRaises(A.workbook_review.ReviewConflict):
            A.workbook_approval.approve(A.ROOT,self.admin,row['id'],baseline,self.authorize)
        result=A.workbook_approval.approve(A.ROOT,self.admin,row['id'],baseline,self.authorize,accept_ambiguous=True)
        self.assertEqual(result['status'],'IMPORTED')
        with closing(A.db()) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM workbook_submission_events WHERE submission_id=? AND event='AMBIGUOUS_ROWS_ACCEPTED'",(row['id'],)).fetchone()[0],1)

    def test_nonadmin_cannot_approve_even_with_valid_csrf(self):
        before=self.fixture.snapshot()
        for role in ('USER','FULL_ACCESS'):
            token=csrf(self.client,self.path if role=='USER' else '/search')
            with closing(A.db()) as c,c: c.execute('UPDATE users SET role=? WHERE id=?',(role,self.admin))
            response=self.client.post(self.path,data={'csrf_token':token,'action':'approve','confirmed':'1',
                'confirm_replacement':'1','baseline':self.baseline})
            self.assertIn(response.status_code,(302,403))
        self.assertEqual(before,self.fixture.snapshot())

    def test_backup_verification_failure_prevents_import(self):
        before=self.fixture.snapshot()
        with patch.object(backups,'verify',side_effect=RuntimeError('Synthetic archive verification failure')):
            with self.assertRaises(A.workbook_approval.ApprovalFailed): self.approve()
        self.assertEqual(before,self.fixture.snapshot()); self.assertEqual(self.stored()['status'],'IMPORT_FAILED')

    def test_actual_result_must_match_reviewed_values(self):
        before=self.fixture.snapshot(); actual=A.workbook_approval.importer.import_workbook
        def drift(path,database,*args,**kwargs):
            if Path(database)==A.DB:
                completed=kwargs['on_success']
                def changed(con,result):
                    con.execute("UPDATE contacts SET address='Synthetic unseen change' WHERE company_id=?",(self.keep,))
                    completed(con,result)
                kwargs['on_success']=changed
            return actual(path,database,*args,**kwargs)
        with patch.object(A.workbook_approval.importer,'import_workbook',side_effect=drift):
            with self.assertRaises(A.workbook_approval.ApprovalFailed): self.approve()
        self.assertEqual(before,self.fixture.snapshot()); self.assertIsNone(self.stored()['import_id'])

    def test_mfa_proof_revoked_during_backup_prevents_import(self):
        before=self.fixture.snapshot(); actual=A.workbook_approval.recovery_backup
        def revoke(*args):
            result=actual(*args)
            with closing(A.db()) as c,c: c.execute('DELETE FROM mfa_session_proofs WHERE user_id=?',(self.admin,))
            return result
        with patch.object(A.workbook_approval,'recovery_backup',side_effect=revoke):
            self.assertEqual(self.post().status_code,403)
        self.assertEqual(before,self.fixture.snapshot()); self.assertIsNone(self.stored()['import_id'])

    def test_changed_archive_never_overwrites_or_imports(self):
        before=self.fixture.snapshot(); archive=A.ROOT/'upload_archive'/(self.row['id']+'.xlsx')
        archive.parent.mkdir(exist_ok=True); archive.write_bytes(b'Synthetic unexpected archive contents')
        with self.assertRaises(A.workbook_approval.ApprovalFailed): self.approve()
        self.assertEqual(archive.read_bytes(),b'Synthetic unexpected archive contents')
        self.assertEqual(before,self.fixture.snapshot())

    def test_original_changed_after_backup_is_rejected_before_data_writes(self):
        before=self.fixture.snapshot(); actual=A.workbook_approval.recovery_backup
        original=A.ROOT/'workbook_submissions'/(self.row['id']+'.xlsx'); contents=original.read_bytes()
        def tamper(*args):
            result=actual(*args); original.write_bytes(b'Synthetic late file change'); return result
        try:
            with patch.object(A.workbook_approval,'recovery_backup',side_effect=tamper):
                with self.assertRaises(A.workbook_approval.ApprovalFailed): self.approve()
            self.assertEqual(before,self.fixture.snapshot()); self.assertIsNone(self.stored()['import_id'])
        finally: original.write_bytes(contents)

if __name__=='__main__': unittest.main()
