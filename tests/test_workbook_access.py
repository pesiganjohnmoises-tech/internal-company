"""Workbook permission foundation on synthetic databases and disposable app copies."""
from pathlib import Path
import runpy
import sqlite3
import unittest
from unittest.mock import patch
from werkzeug.datastructures import MultiDict

state=runpy.run_path(str(Path(__file__).resolve().parent/'test_admin_verification.py'))
A=state['A']; csrf=state['csrf']

class WorkbookAccessTests(unittest.TestCase):
    def setUp(self):
        self.fixture=state['AdminVerificationTests'](); self.fixture.setUp()
        self.client=self.fixture.client; self.uid=self.fixture.target; self.used=0
        with A.db() as c:
            c.execute("UPDATE users SET role='FULL_ACCESS' WHERE id=?",(self.uid,))
            self.country=c.execute("INSERT INTO countries(name) VALUES(?)",('Upload country '+str(self.uid),)).lastrowid
            self.other=c.execute("INSERT INTO countries(name) VALUES(?)",('Other country '+str(self.uid),)).lastrowid
        self.route=f'/admin/users/{self.uid}/workbook-access'

    def grants(self):
        with A.db() as c:
            return sorted(r[0] for r in c.execute('SELECT country_id FROM user_workbook_upload_access WHERE user_id=?',(self.uid,)))

    def save(self,countries=None,enabled=True,verified=True):
        data=MultiDict({'csrf_token':csrf(self.client,self.fixture.base)})
        if enabled: data.add('submit_workbooks','1')
        for country in countries or []: data.add('upload_countries',str(country))
        if verified:
            data.update(self.fixture.creds(self.used)); self.used+=1
        return self.client.post(self.route,data=data)

    def test_defaults_scope_and_no_admin_privileges(self):
        self.assertEqual(self.grants(),[])
        self.assertFalse(A.can_submit_workbooks({'id':self.uid}))
        self.assertEqual(self.save([self.country]).status_code,302)
        self.assertTrue(A.can_submit_workbooks({'id':self.uid},self.country))
        self.assertFalse(A.can_submit_workbooks({'id':self.uid},self.other))
        self.assertFalse(A.can_submit_workbooks({'id':self.fixture.actor}))
        self.assertIn('Full Access + Workbook Upload',self.client.get('/admin/users').get_data(as_text=True))
        user=A.app.test_client()
        with A.db() as c: name=c.execute('SELECT username FROM users WHERE id=?',(self.uid,)).fetchone()[0]
        user.post('/login',data={'username':name,'password':'Synthetic-Target-Password!','csrf_token':csrf(user,'/login')})
        self.assertEqual(user.get('/search').status_code,200)
        for route in ('/admin','/admin/users','/admin/imports'):
            self.assertEqual(user.get(route).status_code,403)
        self.assertEqual(user.post('/admin/imports',data={'csrf_token':csrf(user,'/search')}).status_code,403)

    def test_changes_require_verification_and_revoke_immediately(self):
        self.assertEqual(self.save([self.country],verified=False).status_code,302)
        self.assertEqual(self.grants(),[])
        self.save([self.country])
        self.save([self.other],verified=False); self.assertEqual(self.grants(),[self.country])
        self.save([self.other]); self.assertEqual(self.grants(),[self.other])
        self.save(enabled=False,verified=False); self.assertTrue(A.can_submit_workbooks({'id':self.uid}))
        self.save(enabled=False); self.assertFalse(A.can_submit_workbooks({'id':self.uid}))

    def test_validation_csrf_and_ineligible_role(self):
        token=csrf(self.client,self.fixture.base)
        for data in ({},{'csrf_token':'invalid'}):
            self.assertEqual(self.client.post(self.route,data=data).status_code,400)
        for fields in ([('submit_workbooks','0')],[('submit_workbooks','1'),('submit_workbooks','1')],
                       [('submit_workbooks','1'),('upload_countries','99999999')],
                       [('submit_workbooks','1'),('upload_countries','bad')],
                       [('submit_workbooks','1'),('upload_countries',str(self.country)),('upload_countries',str(self.country))]):
            self.assertEqual(self.client.post(self.route,data=MultiDict([('csrf_token',token),*fields])).status_code,400)
        self.assertEqual(self.save([],verified=False).status_code,302)
        for role in ('USER','ADMIN'):
            with A.db() as c: c.execute('UPDATE users SET role=? WHERE id=?',(role,self.uid))
            self.assertEqual(self.save([self.country],verified=False).status_code,400)
        self.assertEqual(self.grants(),[])

    def test_role_and_status_are_always_checked_and_role_change_clears_grants(self):
        self.save([self.country])
        with A.db() as c: c.execute("UPDATE users SET status='DISABLED' WHERE id=?",(self.uid,))
        self.assertFalse(A.can_submit_workbooks({'id':self.uid}))
        with A.db() as c: c.execute("UPDATE users SET status='ACTIVE' WHERE id=?",(self.uid,))
        self.client.post(self.fixture.base,data={'role':'USER','status':'ACTIVE','csrf_token':csrf(self.client,self.fixture.base),**self.fixture.creds(self.used)})
        self.assertEqual(self.grants(),[])
        with A.db() as c: c.execute("UPDATE users SET role='FULL_ACCESS' WHERE id=?",(self.uid,))
        self.assertFalse(A.can_submit_workbooks({'id':self.uid}))

    def test_nonadmin_cannot_grant_and_normal_edit_preserves_upload_scope(self):
        self.save([self.country])
        self.client.post(self.fixture.base,data={'role':'FULL_ACCESS','status':'ACTIVE','first_name':'Updated','csrf_token':csrf(self.client,self.fixture.base)})
        self.assertEqual(self.grants(),[self.country])
        for role in ('USER','FULL_ACCESS'):
            with A.db() as c: c.execute('UPDATE users SET role=? WHERE id=?',(role,self.fixture.actor))
            self.assertEqual(self.save([self.other],verified=False).status_code,403)
        self.assertEqual(self.grants(),[self.country])

    def test_failed_verification_and_write_do_not_grant_or_consume_factor(self):
        token=csrf(self.client,self.fixture.base)
        self.client.post(self.route,data={'csrf_token':token,'submit_workbooks':'1','upload_countries':str(self.country),
                                         **self.fixture.creds(),'current_password':'wrong'})
        self.assertEqual(self.grants(),[])
        original=A.verify_admin_change
        def failed(con,inputs,destination):
            original(con,inputs,destination)
            raise A.sqlite3.OperationalError('Synthetic write failure')
        with patch.object(A,'verify_admin_change',side_effect=failed):
            self.assertEqual(self.save([self.country]).status_code,503)
        self.assertEqual(self.grants(),[])
        with A.db() as c:
            self.assertIsNone(c.execute('SELECT used_at FROM mfa_recovery_codes WHERE user_id=? AND code_hash=?',
                                       (self.fixture.actor,A.mfa.recovery_hash(self.fixture.codes[0]))).fetchone()[0])

    def test_stale_review_rolls_back_without_consuming_admin_code(self):
        original=A.prepare_admin_verification
        def concurrent_change(destination):
            inputs=original(destination)
            with A.db() as c: c.execute('INSERT INTO user_workbook_upload_access VALUES(?,?)',(self.uid,self.other))
            return inputs
        with patch.object(A,'prepare_admin_verification',side_effect=concurrent_change):
            self.assertEqual(self.save([self.country]).status_code,409)
        self.assertEqual(self.grants(),[self.other])
        with A.db() as c:
            self.assertIsNone(c.execute('SELECT used_at FROM mfa_recovery_codes WHERE user_id=? AND code_hash=?',
                                       (self.fixture.actor,A.mfa.recovery_hash(self.fixture.codes[0]))).fetchone()[0])

    def test_schema_is_additive_repeatable_and_does_not_grant_existing_users(self):
        with sqlite3.connect(':memory:') as c:
            c.execute('PRAGMA foreign_keys=ON')
            c.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY,role TEXT); CREATE TABLE countries(id INTEGER PRIMARY KEY); INSERT INTO users VALUES(1,'FULL_ACCESS'); INSERT INTO countries VALUES(2);")
            c.executescript(A.workbook_access.SCHEMA); c.executescript(A.workbook_access.SCHEMA)
            self.assertEqual(c.execute('SELECT * FROM users').fetchall(),[(1,'FULL_ACCESS')])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM user_workbook_upload_access').fetchone()[0],0)
            c.execute('INSERT INTO user_workbook_upload_access VALUES(1,2)')
            c.execute('DELETE FROM countries WHERE id=2')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM user_workbook_upload_access').fetchone()[0],0)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

if __name__=='__main__': unittest.main()
