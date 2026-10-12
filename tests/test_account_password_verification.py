"""Self-password verification checks on disposable copies and synthetic enrolled admins."""
import runpy, unittest
from pathlib import Path
from unittest.mock import patch
state=runpy.run_path(str(Path(__file__).resolve().parent/"test_admin_verification.py"))
A=state["A"]; csrf=state["csrf"]; base=state["AdminVerificationTests"]

class PasswordVerificationTests(unittest.TestCase):
    serial=0; setUp=base.setUp; snapshot=base.snapshot; creds=base.creds
    new_password="Synthetic-New-Self-Password!"
    def change(self,creds=None):
        return self.client.post("/account/password",data={"csrf_token":csrf(self.client,"/account/password"),"new_password":self.new_password,"confirm_password":self.new_password,**(creds or {})})
    def test_missing_wrong_and_replayed_enrollment_factor_rejected(self):
        for credentials in ({},{"current_password":self.password},{**self.creds(),"current_password":"wrong"},{**self.creds(),"code":"0000-0000-0000-0000-0000"},{"current_password":self.password,"code":self.enrollment_code}):
            before=self.snapshot(); self.change(credentials); self.assertEqual(before,self.snapshot())
            with A.db() as c: c.execute("DELETE FROM security_events")
    def test_success_revokes_other_browser_and_recovery_replay(self):
        other=A.app.test_client()
        other.post("/login",data={"username":self.name,"password":self.password,"csrf_token":csrf(other,"/login")})
        other.post("/login/mfa",data={"method":"recovery","code":self.codes[1],"csrf_token":csrf(other,"/login/mfa")})
        self.assertEqual(other.get("/admin").status_code,200)
        self.assertEqual(self.change(self.creds()).status_code,302)
        self.assertEqual(self.client.get("/admin").status_code,200); self.assertEqual(other.get("/admin").status_code,302)
        with A.db() as c: self.assertTrue(A.check_password_hash(c.execute("SELECT password_hash FROM users WHERE id=?",(self.actor,)).fetchone()[0],self.new_password))
        before=self.snapshot(); self.change({**self.creds(),"current_password":self.new_password,"new_password":"Synthetic-Another-Password!","confirm_password":"Synthetic-Another-Password!"}); self.assertEqual(before,self.snapshot())
    def test_failed_session_creation_rolls_back_password_factor_and_cookie(self):
        before=self.snapshot(); original=A.start_session
        with self.client.session_transaction() as s: old_sid=s["sid"]
        def failed(*args,**kwargs): original(*args,**kwargs); raise A.sqlite3.OperationalError("synthetic private failure")
        with patch.object(A,"start_session",side_effect=failed): response=self.change(self.creds())
        self.assertEqual(response.status_code,503); self.assertEqual(before,self.snapshot()); self.assertNotIn(b"synthetic private failure",response.data)
        with self.client.session_transaction() as s: self.assertEqual(s["sid"],old_sid)
        self.assertEqual(self.client.get("/admin").status_code,200)
    def test_fresh_totp_success(self):
        with A.db() as c: step=c.execute("SELECT last_used_step FROM user_mfa WHERE user_id=?",(self.actor,)).fetchone()[0]+1
        stamp=step*30+1; code=A.mfa.pyotp.TOTP(self.secret).at(stamp)
        with patch.object(A.mfa.time,"time",return_value=stamp):
            self.assertEqual(self.change({"current_password":self.password,"method":"totp","code":code}).status_code,302)
        with A.db() as c:
            self.assertEqual(c.execute("SELECT last_used_step FROM user_mfa WHERE user_id=?",(self.actor,)).fetchone()[0],step)
            self.assertTrue(A.check_password_hash(c.execute("SELECT password_hash FROM users WHERE id=?",(self.actor,)).fetchone()[0],self.new_password))
    def test_revoked_during_preflight_rejected(self):
        original=A.prepare_admin_verification
        with A.db() as c: old_hash=c.execute("SELECT password_hash FROM users WHERE id=?",(self.actor,)).fetchone()[0]
        def revoked(url):
            values=original(url)
            with A.db() as c: c.execute("DELETE FROM security_sessions WHERE user_id=?",(self.actor,))
            return values
        with patch.object(A,"prepare_admin_verification",side_effect=revoked): self.change(self.creds())
        with A.db() as c: self.assertEqual(c.execute("SELECT password_hash FROM users WHERE id=?",(self.actor,)).fetchone()[0],old_hash)
    def test_mfa_off_password_only_and_conditional_form(self):
        self.assertIn(b'name="code"',self.client.get("/account/password").data)
        with patch.dict(A.app.config,MFA_ENABLED=False,MFA_ENROLLMENT_ENABLED=False):
            self.assertNotIn(b'name="code"',self.client.get("/account/password").data)
            self.assertEqual(self.change({"current_password":self.password}).status_code,302)
            self.assertEqual(self.client.get("/admin").status_code,200)

del base
if __name__=="__main__": unittest.main()
