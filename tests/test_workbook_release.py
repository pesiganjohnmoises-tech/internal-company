"""Release acceptance: isolated migration preservation and submit/review/import flows."""
from contextlib import closing
import io
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

state=runpy.run_path(str(Path(__file__).resolve().parent/'test_workbook_approval.py'))
A=state['A']; csrf=state['csrf']

class WorkbookReleaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture=state['WorkbookApprovalTests'](); self.fixture.setUp()
        self.review=self.fixture.fixture

    def test_repeat_initialization_preserves_all_healthy_records_and_grants(self):
        tables=('users','countries','companies','contacts','imports','user_country_access','user_company_access',
                'user_field_access','user_workbook_upload_access','workbook_submissions','workbook_submission_events',
                'user_mfa','mfa_recovery_codes','mfa_session_proofs')
        def snapshot():
            with closing(A.db()) as c:
                return {t:[tuple(r) for r in c.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in tables}
        before=snapshot(); A.init_db(); A.init_db(); self.assertEqual(before,snapshot())
        with closing(A.db()) as c:
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_submitter_admin_confirmation_import_and_revocation(self):
        user=A.app.test_client()
        with closing(A.db()) as c: name=c.execute('SELECT username FROM users WHERE id=?',(self.fixture.uid,)).fetchone()[0]
        user.post('/login',data={'username':name,'password':'Synthetic-Target-Password!','csrf_token':csrf(user,'/login')})
        payload=self.review.workbook([['Keep','Alice','release@example.test','Synthetic updated address'],['New release company','New person','new@example.test','New address']])
        before=self.review.snapshot()
        result=user.post('/workbooks/upload',data={'csrf_token':csrf(user,'/workbooks/upload'),
            'country_id':str(self.fixture.cid),'complete_country':'1','workbook':(io.BytesIO(payload),'release.xlsx')})
        self.assertEqual(result.status_code,302); owner_path=result.location; sid=owner_path.split('/')[-1]
        self.assertEqual(before,self.review.snapshot())
        admin_path='/admin/workbooks/'+sid; admin=self.fixture.client
        self.assertEqual(user.get(admin_path).status_code,403)
        preview=A.workbook_review.preview(A.ROOT,self.fixture.admin,sid); baseline=preview['baseline']
        fields={'csrf_token':csrf(admin,admin_path),'action':'review','confirmed':'1','baseline':baseline}
        self.assertEqual(admin.post(admin_path,data=fields).status_code,302)
        fields.update(action='approve',confirmed='')
        self.assertEqual(admin.post(admin_path,data=fields).status_code,200)
        self.assertEqual(admin.get(admin_path).status_code,200); self.assertEqual(before,self.review.snapshot())
        fields.update(confirmed='1',confirm_replacement='1')
        self.assertEqual(admin.post(admin_path,data=fields).status_code,302)
        self.assertIn('approved and imported',user.get(owner_path).get_data(as_text=True))
        with closing(A.db()) as c,c:
            self.assertEqual(c.execute('SELECT email FROM contacts WHERE company_id=?',(self.fixture.keep,)).fetchone()[0],'release@example.test')
            c.execute('DELETE FROM user_workbook_upload_access WHERE user_id=?',(self.fixture.uid,))
        self.assertEqual(user.get(owner_path).status_code,403)
        self.assertEqual(user.get(owner_path+'/download').status_code,403)
        self.assertEqual(admin.get(admin_path).status_code,200)

    def test_expired_csrf_cannot_approve(self):
        before=self.review.snapshot(); token=csrf(self.fixture.client,self.fixture.path)
        with patch.dict(A.app.config,WTF_CSRF_TIME_LIMIT=-1):
            result=self.fixture.client.post(self.fixture.path,data={'csrf_token':token,'action':'approve',
                'confirmed':'1','confirm_replacement':'1','baseline':self.fixture.baseline})
        self.assertEqual(result.status_code,400); self.assertEqual(before,self.review.snapshot())

    def test_existing_production_mfa_gate_is_a_confirmed_rollout_blocker(self):
        env={**os.environ,'APP_ENV':'production','SECRET_KEY':'synthetic-production-session-key-'*2,
             'COOKIE_SECURE':'1','MFA_ENABLED':'1'}
        result=subprocess.run([sys.executable,'-B','-c','import app'],cwd=A.ROOT,env=env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('MFA rollout is restricted to development',result.stderr)

if __name__=='__main__': unittest.main()
