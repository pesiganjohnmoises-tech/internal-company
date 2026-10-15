"""Release acceptance: isolated migration preservation and submit/review/import flows."""
from contextlib import closing
import io
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
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

    def test_production_mfa_requires_valid_key_and_migrated_storage(self):
        env={**os.environ,'APP_ENV':'production','SECRET_KEY':'synthetic-production-session-key-'*2,
             'COOKIE_SECURE':'1','MFA_ENABLED':'1'}
        result=subprocess.run([sys.executable,'-B','-c','import app; assert app.app.config["MFA_ENABLED"]; assert app.app.config["SESSION_COOKIE_SECURE"]'],cwd=A.ROOT,env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        for changes in ({'MFA_ENCRYPTION_KEY':'invalid','MFA_ENCRYPTION_KEY_FILE':''},
                        {'MFA_ENCRYPTION_KEY':'','MFA_ENCRYPTION_KEY_FILE':''},
                        {'COOKIE_SECURE':'0'},{'FLASK_DEBUG':'1'},{'SECRET_KEY':''}):
            result=subprocess.run([sys.executable,'-B','-c','import app'],cwd=A.ROOT,
                                  env={**env,**changes},capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
        # A fresh code-only copy has no migrated database; startup must not create it.
        folder=Path(tempfile.mkdtemp(prefix='directory-production-mfa-schema-'))
        for name in ('app.py','utils','templates','static'):
            source=A.ROOT/name
            if source.is_dir(): shutil.copytree(source,folder/name,ignore=shutil.ignore_patterns('__pycache__'))
            else: shutil.copy2(source,folder/name)
        result=subprocess.run([sys.executable,'-B','-c','import app'],cwd=folder,env=env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('explicit storage migration',result.stderr)
        self.assertFalse((folder/'directory.db').exists())

    def test_production_required_enrollment_login_and_restart(self):
        env={**os.environ,'APP_ENV':'production','SECRET_KEY':'synthetic-production-session-key-'*2,
             'COOKIE_SECURE':'1','MFA_ENABLED':'1','FLASK_DEBUG':'0'}
        code=r'''
import app as A, re, pyotp
def token(client,path):
    return re.search(r'name="csrf_token" value="([^"]+)"',client.get(path).get_data(as_text=True)).group(1)
for role in ('ADMIN','USER','FULL_ACCESS'):
    name='production-'+role
    with A.db() as c:
        uid=c.execute("INSERT INTO users(username,password_hash,role,status) VALUES(?,?,?,'ACTIVE')",
                      (name,A.generate_password_hash('Synthetic-Production-Password!'),role)).lastrowid
        c.execute('INSERT INTO user_mfa(user_id,required) VALUES(?,1)',(uid,))
    client=A.app.test_client()
    response=client.post('/login',data={'username':name,'password':'Synthetic-Production-Password!',
                                     'csrf_token':token(client,'/login')})
    assert response.status_code==302
    assert client.get('/search').location=='/login/mfa'
    html=client.get('/account/mfa').get_data(as_text=True)
    secret=re.search(r'<code class="mfa-key">([A-Z2-7]{32})</code>',html).group(1)
    response=client.post('/account/mfa',data={'action':'confirm','code':pyotp.TOTP(secret).now(),
                                           'csrf_token':token(client,'/account/mfa')})
    assert response.status_code==200
    recovery=re.findall(r'<code>((?:[A-F0-9]{4}-){4}[A-F0-9]{4})</code>',response.get_data(as_text=True))[0]
    assert client.get('/search').status_code==200
    assert client.get('/admin').status_code==(200 if role=='ADMIN' else 403)
    other=A.app.test_client()
    other.post('/login',data={'username':name,'password':'Synthetic-Production-Password!',
                             'csrf_token':token(other,'/login')})
    assert other.get('/search').location=='/login/mfa'
    response=other.post('/login/mfa',data={'method':'recovery','code':recovery,'csrf_token':token(other,'/login/mfa')})
    assert response.status_code==302
    assert other.get('/search').status_code==200
    assert 'Secure' in response.headers.get('Set-Cookie','')
'''
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=A.ROOT,env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        result=subprocess.run([sys.executable,'-B','-c',
            'import app; assert app.app.config["MFA_ENABLED"]; '
            'assert app.db().execute("SELECT count(*) FROM user_mfa WHERE enabled_at IS NOT NULL").fetchone()[0]>=3'],
            cwd=A.ROOT,env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__': unittest.main()
