"""Admin MFA reset regression checks on disposable code and synthetic data."""
from pathlib import Path
import re
import runpy
import unittest
from unittest.mock import patch

state=runpy.run_path(str(Path(__file__).resolve().parent/'test_admin_verification.py'))
A=state['A']; csrf=state['csrf']

class AdminMFAResetTests(unittest.TestCase):
    def setUp(self):
        self.fixture=state['AdminVerificationTests'](); self.fixture.setUp()
        self.admin=self.fixture.client; self.uid=self.fixture.target
        self.route=f'/admin/users/{self.uid}/mfa-reset'

    def reset(self,client=None,token=None):
        client=self.admin if client is None else client
        return client.post(self.route,data={'csrf_token':csrf(client,self.fixture.base) if token is None else token})

    def snapshot(self):
        with A.db() as c:
            return {table:[tuple(row) for row in c.execute('SELECT * FROM '+table)] for table in
                    ('users','user_mfa','mfa_recovery_codes','mfa_challenges','user_country_access','user_company_access','user_field_access')}

    def test_reset_needs_only_valid_csrf_and_verified_admin_session(self):
        before=self.snapshot()
        response=self.reset()
        self.assertEqual(response.status_code,302)
        with A.db() as c:
            flags=A.mfa.account_state(c,dict(c.execute('SELECT * FROM users WHERE id=?',(self.uid,)).fetchone()))
            self.assertTrue(flags['recovery_required']); self.assertFalse(flags['enabled'])
            actor_codes=[tuple(r) for r in c.execute('SELECT * FROM mfa_recovery_codes WHERE user_id=?',(self.fixture.actor,))]
        self.assertEqual(set(actor_codes),{r for r in before['mfa_recovery_codes'] if r[0]==self.fixture.actor})

    def test_invalid_missing_and_expired_csrf_do_not_reset(self):
        before=self.snapshot()
        for data in ({},{'csrf_token':'invalid'}):
            self.assertEqual(self.admin.post(self.route,data=data).status_code,400)
            self.assertEqual(self.snapshot(),before)
        token=csrf(self.admin,self.fixture.base)
        with patch.dict(A.app.config,WTF_CSRF_TIME_LIMIT=-1):
            self.assertEqual(self.reset(token=token).status_code,400)
        self.assertEqual(self.snapshot(),before)

    def test_self_reset_nonadmin_and_unverified_sessions_are_denied(self):
        before=self.snapshot()
        token=csrf(self.admin,self.fixture.base)
        self.assertEqual(self.admin.post(f'/admin/users/{self.fixture.actor}/mfa-reset',data={'csrf_token':token}).status_code,400)
        for role in ('USER','FULL_ACCESS'):
            with A.db() as c: c.execute('UPDATE users SET role=? WHERE id=?',(role,self.fixture.actor))
            self.assertEqual(self.reset().status_code,403)
        with A.db() as c: c.execute("UPDATE users SET role='ADMIN' WHERE id=?",(self.fixture.actor,))
        with self.admin.session_transaction() as s: s['mfa_verified']=False
        self.assertEqual(self.reset(token=token).status_code,302)
        after=self.snapshot()
        self.assertEqual(before['user_mfa'],after['user_mfa'])
        self.assertEqual(before['mfa_recovery_codes'],after['mfa_recovery_codes'])

    def test_reset_failure_rolls_back_factor_and_session_revocation(self):
        before=self.snapshot(); original=A.mfa.reset_factor
        def failed(con,uid):
            original(con,uid)
            raise A.sqlite3.OperationalError('Synthetic reset failure')
        with patch.object(A.mfa,'reset_factor',side_effect=failed):
            self.assertEqual(self.reset().status_code,503)
        self.assertEqual(before,self.snapshot())
        self.assertEqual(self.admin.get('/admin').status_code,200)

    def test_revoked_admin_role_is_rechecked_before_reset(self):
        before=self.snapshot(); original=A.current_user; changed=False
        def changed_role():
            nonlocal changed
            user=original()
            if user and not changed:
                with A.db() as c: c.execute("UPDATE users SET role='USER' WHERE id=?",(self.fixture.actor,))
                changed=True
            return user
        token=csrf(self.admin,self.fixture.base)
        with patch.object(A,'current_user',side_effect=changed_role):
            result=self.reset(token=token)
        self.assertEqual(result.location,'/login')
        self.assertEqual(before['user_mfa'],self.snapshot()['user_mfa'])

    def test_reset_form_has_no_extra_verification_and_retains_csrf(self):
        html=self.admin.get(self.fixture.base).get_data(as_text=True)
        form=re.search(r'<form[^>]*data-mfa-reset-form[^>]*>(.*?)</form>',html,re.S).group(1)
        self.assertIn('name="csrf_token"',form)
        for field in ('current_password','method','code','identity_verified','confirm_user_id'):
            self.assertNotIn(f'name="{field}"',form)
        self.assertIn('Yes, reset MFA',form); self.assertIn('Cancel',form)

    def test_reset_preserves_identity_and_policy_and_requires_reenrollment(self):
        for required in (0,1):
            if required: self.setUp()
            with A.db() as c:
                target=dict(c.execute('SELECT * FROM users WHERE id=?',(self.uid,)).fetchone())
                c.execute('INSERT OR IGNORE INTO user_mfa(user_id) VALUES(?)',(self.uid,))
                c.execute('UPDATE user_mfa SET required=? WHERE user_id=?',(required,self.uid))
                # Give a synthetic grant to verify reset does not change permissions.
                c.execute("INSERT INTO user_field_access VALUES(?,'company_name')",(self.uid,))
            password='Synthetic-Target-Password!'
            def login():
                client=A.app.test_client()
                client.post('/login',data={'username':target['username'],'password':password,'csrf_token':csrf(client,'/login')})
                return client
            client=login()
            if not required:
                client.post('/account/mfa',data={'action':'start','current_password':password,'csrf_token':csrf(client)})
            secret=re.search(r'<code class="mfa-key">([A-Z2-7]{32})</code>',client.get('/account/mfa').get_data(as_text=True)).group(1)
            enrolled=client.post('/account/mfa',data={'action':'confirm','code':state['state']['pyotp'].TOTP(secret).now(),'csrf_token':csrf(client)})
            old_codes=re.findall(r'<code>((?:[A-F0-9]{4}-){4}[A-F0-9]{4})</code>',enrolled.get_data(as_text=True))
            pending=login()
            before=self.snapshot()
            with A.db() as c: target=dict(c.execute('SELECT * FROM users WHERE id=?',(self.uid,)).fetchone())
            self.assertEqual(self.reset().status_code,302)
            self.assertEqual(client.get('/search').location,'/login')
            self.assertEqual(pending.get('/login/mfa').location,'/login')
            with A.db() as c:
                self.assertEqual(dict(c.execute('SELECT * FROM users WHERE id=?',(self.uid,)).fetchone()),target)
                self.assertEqual(c.execute('SELECT required FROM user_mfa WHERE user_id=?',(self.uid,)).fetchone()[0],required)
                for table in ('mfa_recovery_codes','mfa_challenges','security_sessions'):
                    self.assertEqual(c.execute('SELECT COUNT(*) FROM '+table+' WHERE user_id=?',(self.uid,)).fetchone()[0],0)
            self.assertEqual(before['user_field_access'],self.snapshot()['user_field_access'])
            client=login()
            self.assertEqual(client.get('/search').location,'/login/mfa')
            html=client.get('/account/mfa').get_data(as_text=True)
            replacement=re.search(r'<code class="mfa-key">([A-Z2-7]{32})</code>',html).group(1)
            self.assertNotEqual(secret,replacement)
            completed=client.post('/account/mfa',data={'action':'confirm','code':state['state']['pyotp'].TOTP(replacement).now(),'csrf_token':csrf(client)})
            self.assertEqual(completed.status_code,200)
            with A.db() as c:
                hashes={r[0] for r in c.execute('SELECT code_hash FROM mfa_recovery_codes WHERE user_id=?',(self.uid,))}
            self.assertTrue(all(A.mfa.recovery_hash(code) not in hashes for code in old_codes))
            again=login()
            codes=re.findall(r'<code>((?:[A-F0-9]{4}-){4}[A-F0-9]{4})</code>',completed.get_data(as_text=True))
            self.assertEqual(again.post('/login/mfa',data={'method':'recovery','code':codes[0],'csrf_token':csrf(again,'/login/mfa')}).status_code,302)
            self.assertEqual(again.get('/search').status_code,200)

if __name__=='__main__': unittest.main()
