"""Account restrictions on disposable app copies and synthetic MFA records."""
import re
import runpy
import unittest
from pathlib import Path

state=runpy.run_path(str(Path(__file__).resolve().parent/'test_mfa_login.py'))
A=state['A']; csrf=state['csrf']; base=state['MFALoginTests']

class AccountRestrictionTests(unittest.TestCase):
    serial=0
    setUp=base.setUp
    login=base.login
    post=base.post
    start=base.start
    confirm=base.confirm
    enroll=base.enroll
    verify=base.verify

    def snapshot(self):
        with A.db() as con:
            return {table:[tuple(row) for row in con.execute('SELECT * FROM '+table)]
                    for table in ('users','user_mfa','mfa_recovery_codes','mfa_challenges')}

    def test_nonadmins_cannot_change_password_or_manage_factors(self):
        self.enroll()
        for role in ('USER','FULL_ACCESS'):
            with A.db() as con: con.execute('UPDATE users SET role=? WHERE id=?',(role,self.uid))
            html=self.client.get('/account/mfa').get_data(as_text=True)
            self.assertNotIn('action="/account/mfa/manage"',html)
            self.assertNotIn('href="/account/password"',html)
            self.assertIn('Account security',html)
            before=self.snapshot()
            self.assertEqual(self.client.get('/account/password').status_code,403)
            token=csrf(self.client)
            for route,data in [('/account/password',{'new_password':'Synthetic-Changed-Password!','confirm_password':'Synthetic-Changed-Password!'}),
                               *[('/account/mfa/manage',{'action':action}) for action in ('replace','disable','regenerate')],
                               *[('/account/mfa',{'action':action}) for action in ('start','confirm','cancel')],
                               (f'/admin/users/{self.uid}/mfa-reset',{})]:
                with self.subTest(role=role,route=route,data=data):
                    response=self.client.post(route,data={**data,'csrf_token':token,'current_password':self.password,'method':'recovery','code':self.codes[0]})
                    self.assertEqual(response.status_code,403)
                    self.assertEqual(self.snapshot(),before)
            self.assertEqual(self.client.get('/account/mfa/qr').status_code,403)
            self.assertEqual(self.client.post('/account/mfa/manage',data={'action':'disable'}).status_code,400)

    def test_required_nonadmins_can_enroll_and_sign_in_with_mfa(self):
        for role in ('USER','FULL_ACCESS'):
            if role=='FULL_ACCESS': self.setUp()
            with A.db() as con:
                con.execute('UPDATE users SET role=? WHERE id=?',(role,self.uid))
                con.execute('INSERT OR IGNORE INTO user_mfa(user_id) VALUES(?)',(self.uid,))
                con.execute('UPDATE user_mfa SET required=1 WHERE user_id=?',(self.uid,))
            self.client=self.login()
            html=self.client.get('/account/mfa').get_data(as_text=True)
            secret=re.search(r'<code class="mfa-key">([A-Z2-7]{32})</code>',html).group(1)
            self.assertEqual(self.client.get('/account/mfa/qr').status_code,200)
            result=self.confirm(secret); self.assertEqual(result.status_code,200)
            self.codes=re.findall(r'<code>((?:[A-F0-9]{4}-){4}[A-F0-9]{4})</code>',result.get_data(as_text=True))
            other=self.login()
            self.assertEqual(self.verify(other,self.codes[1],'recovery').status_code,302)
            self.assertEqual(other.get('/search').status_code,200)
            self.assertEqual(self.client.get('/search').status_code,200)
            self.assertEqual(self.client.get('/account/password').status_code,403)

    def test_old_pending_replacement_is_not_exposed_or_completed(self):
        self.enroll()
        with A.db() as con:
            user=dict(con.execute('SELECT * FROM users WHERE id=?',(self.uid,)).fetchone())
        with self.client.session_transaction() as session: sid=session['sid']
        with A.db() as con:
            token=A.mfa.begin_replacement(con,A.app.extensions['mfa_cipher'],user,sid)
        with self.client.session_transaction() as session: session['mfa_enrollment']=token
        before=self.snapshot()
        self.assertNotIn('class="mfa-key"',self.client.get('/account/mfa').get_data(as_text=True))
        self.assertEqual(self.client.get('/account/mfa/qr').status_code,403)
        with A.db() as con:
            with self.assertRaises(A.mfa.MFAEnrollmentError):
                A.mfa.pending_enrollment(con,A.app.extensions['mfa_cipher'],self.uid,token,sid)
        self.assertEqual(self.snapshot(),before)

del base
if __name__=='__main__': unittest.main()
