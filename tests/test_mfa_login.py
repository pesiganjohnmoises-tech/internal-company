"""Step 4: isolated MFA login, session proofs, replay and multi-process tests."""
import concurrent.futures
from pathlib import Path
import os
import re
import runpy
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

state=runpy.run_path(str(Path(__file__).resolve().parent/"test_mfa_enrollment.py"))
A=state["A"]; TMP=state["TMP"]; csrf=state["csrf"]
pyotp=state["pyotp"]


class MFALoginTests(unittest.TestCase):
    serial=0
    setUp=state["EnrollmentTests"].setUp
    login=state["EnrollmentTests"].login
    post=state["EnrollmentTests"].post
    start=state["EnrollmentTests"].start
    confirm=state["EnrollmentTests"].confirm

    def enroll(self):
        self.secret=self.start()
        result=self.confirm(self.secret)
        self.assertEqual(result.status_code,200)
        self.codes=re.findall(r"<code>((?:[A-F0-9]{4}-){4}[A-F0-9]{4})</code>",result.get_data(as_text=True))
        self.stamp=int(time.time())//30*30+45
        self.code=pyotp.TOTP(self.secret).at(self.stamp)

    def pending(self):
        client=self.login()
        with client.session_transaction() as session:
            self.assertIn("mfa_pending",session)
            self.assertNotIn("uid",session); self.assertNotIn("sid",session)
        return client

    def verify(self,client,code,method="totp",ip="127.0.0.1"):
        return client.post("/login/mfa",data={"code":code,"method":method,"csrf_token":csrf(client,"/login/mfa")},environ_overrides={"REMOTE_ADDR":ip})

    def pending_token(self,client):
        with client.session_transaction() as session: return session["mfa_pending"]

    def test_password_only_staff_and_full_access_remain_supported(self):
        self.assertEqual(self.client.get("/search").status_code,200)
        self.assertEqual(self.client.get("/admin").status_code,403)
        with self.client.session_transaction() as session:
            self.assertFalse(session["mfa_verified"])
            sid=session["sid"]
        with A.db() as con:
            self.assertEqual(con.execute("SELECT verified FROM mfa_session_proofs WHERE token_hash=?",(A.security.opaque(sid),)).fetchone()[0],0)
            con.execute("UPDATE users SET role='FULL_ACCESS' WHERE id=?",(self.uid,))
        other=self.login()
        self.assertEqual(other.get("/search").status_code,200)
        self.assertEqual(other.get("/admin").status_code,403)

    def test_admin_password_only_is_restricted_to_enrollment(self):
        with A.db() as con:
            con.execute("UPDATE users SET role='ADMIN',last_login_at=NULL WHERE id=?",(self.uid,))
        self.assertEqual(self.client.get("/admin").location,"/login")
        client=self.pending()
        for route in ("/search","/company/1","/admin","/admin/users","/admin/imports","/admin/export/companies.csv"):
            result=client.get(route)
            self.assertEqual(result.status_code,302,route)
            self.assertEqual(result.location,"/login/mfa",route)
        with A.db() as con:
            self.assertIsNone(con.execute("SELECT last_login_at FROM users WHERE id=?",(self.uid,)).fetchone()[0])
            self.assertEqual(con.execute("SELECT COUNT(*) FROM security_sessions WHERE user_id=?",(self.uid,)).fetchone()[0],0)
        html=client.get("/account/mfa").get_data(as_text=True)
        self.assertIn('class="mfa-key"',html)
        self.assertNotIn("Admin</span>",html)
        secret=re.search(r'<code class="mfa-key">([A-Z2-7]{32})</code>',html).group(1)
        result=client.post("/account/mfa",data={"action":"confirm","code":pyotp.TOTP(secret).now(),"csrf_token":csrf(client)})
        self.assertEqual(result.status_code,200)
        self.assertEqual(client.get("/admin").status_code,200)
        with client.session_transaction() as session: self.assertTrue(session["mfa_verified"])

    def test_pending_post_cannot_modify_users_or_imports(self):
        self.enroll(); client=self.pending()
        token=csrf(client,"/login/mfa")
        for route,data in (("/admin/users/create",{"username":"blockednew","password":"Synthetic-New-Password!"}),
                           (f"/admin/users/{self.uid}",{"role":"ADMIN","status":"ACTIVE"}),
                           ("/admin/imports/preview",{"filename":"synthetic.xlsx"})):
            result=client.post(route,data={**data,"csrf_token":token})
            self.assertEqual(result.status_code,302)
        with A.db() as con:
            self.assertIsNone(con.execute("SELECT id FROM users WHERE username='blockednew'").fetchone())
            self.assertEqual(con.execute("SELECT role FROM users WHERE id=?",(self.uid,)).fetchone()[0],"USER")

    def test_totp_creates_server_proof_only_after_verification(self):
        self.enroll()
        with A.db() as con: con.execute("UPDATE users SET last_login_at=NULL WHERE id=?",(self.uid,))
        client=self.pending()
        self.assertEqual(client.get("/account/mfa/qr").location,"/login/mfa")
        result=self.verify(client,self.code)
        self.assertEqual(result.status_code,302); self.assertEqual(result.location,"/search")
        self.assertEqual(client.get("/search").status_code,200)
        self.assertEqual(client.get("/admin").status_code,403)
        with client.session_transaction() as session:
            self.assertTrue(session["mfa_verified"]); self.assertNotIn("mfa_pending",session)
            sid=session["sid"]
        with A.db() as con:
            self.assertEqual(con.execute("SELECT verified FROM mfa_session_proofs WHERE token_hash=?",(A.security.opaque(sid),)).fetchone()[0],1)
            self.assertIsNotNone(con.execute("SELECT last_login_at FROM users WHERE id=?",(self.uid,)).fetchone()[0])

    def test_same_totp_cannot_be_used_by_another_browser(self):
        self.enroll(); first=self.pending(); second=self.pending()
        self.assertEqual(self.verify(first,self.code).status_code,302)
        self.assertEqual(self.verify(second,self.code).status_code,200)
        self.assertEqual(second.get("/search").location,"/login/mfa")

    def test_recovery_code_is_single_use_across_logins(self):
        self.enroll(); first=self.pending(); second=self.pending()
        self.assertEqual(self.verify(first,self.codes[0].lower(),"recovery").status_code,302)
        self.assertEqual(self.verify(second,self.codes[0],"recovery").status_code,200)
        self.assertEqual(second.get("/search").location,"/login/mfa")
        with A.db() as con:
            self.assertIsNotNone(con.execute("SELECT used_at FROM mfa_recovery_codes WHERE user_id=? AND code_hash=?",(self.uid,A.mfa.recovery_hash(self.codes[0]))).fetchone()[0])

    def test_expired_challenge_and_cancel_do_not_grant_access(self):
        self.enroll(); client=self.pending()
        with A.db() as con:
            con.execute("UPDATE mfa_challenges SET created_at=?,expires_at=? WHERE token_hash=?",(time.time()-600,time.time()-1,A.security.opaque(self.pending_token(client))))
        self.assertEqual(client.get("/login/mfa").location,"/login")
        self.assertEqual(client.get("/search").location,"/login")
        client=self.pending(); token=self.pending_token(client)
        result=client.post("/login/mfa",data={"method":"cancel","csrf_token":csrf(client,"/login/mfa")})
        self.assertEqual(result.location,"/login")
        with client.session_transaction() as session: session["mfa_pending"]=token
        self.assertEqual(client.get("/login/mfa").location,"/login")

    def test_csrf_and_input_limits(self):
        self.enroll(); client=self.pending()
        for fields in ({},{"csrf_token":"invalid"}):
            self.assertEqual(client.post("/login/mfa",data={"code":self.codes[0],"method":"recovery",**fields}).status_code,400)
        token=csrf(client,"/login/mfa")
        with patch.dict(A.app.config,WTF_CSRF_TIME_LIMIT=-1):
            self.assertEqual(client.post("/login/mfa",data={"code":self.code,"csrf_token":token}).status_code,400)
        for method,code in (("totp","1"*7),("recovery","A"*25),("unknown","123456")):
            with patch.object(A.mfa,"verify_login") as verifier:
                self.assertEqual(self.verify(client,code,method).status_code,400)
                verifier.assert_not_called()

    def test_failures_persist_after_new_password_login_and_ip_changes(self):
        self.enroll(); client=self.pending()
        for i in range(5): self.assertEqual(self.verify(client,"abcdef",ip=f"192.0.2.{i+1}").status_code,200)
        other=self.pending()
        result=self.verify(other,self.codes[0],"recovery",ip="203.0.113.1")
        self.assertEqual(result.status_code,429); self.assertIn("Retry-After",result.headers)

    def test_policy_enforcement_revokes_completed_password_only_session(self):
        with A.db() as con:
            con.execute("INSERT INTO user_mfa(user_id,required,security_version) VALUES(?,1,1)",(self.uid,))
        self.assertEqual(self.client.get("/search").location,"/login")
        pending=self.pending()
        self.assertIn('class="mfa-key"',pending.get("/account/mfa").get_data(as_text=True))

    def test_policy_removal_preserves_enabled_factor(self):
        self.enroll()
        with A.db() as con: con.execute("UPDATE user_mfa SET required=0 WHERE user_id=?",(self.uid,))
        self.assertIn("Authenticator code",self.pending().get("/login/mfa").get_data(as_text=True))

    def test_password_role_status_and_policy_changes_invalidate_pending(self):
        self.enroll()
        for column,value in (("role","FULL_ACCESS"),("status","DISABLED"),("password_hash","synthetic-replaced-hash")):
            client=self.pending()
            with A.db() as con:
                old=con.execute(f"SELECT {column} FROM users WHERE id=?",(self.uid,)).fetchone()[0]
                con.execute(f"UPDATE users SET {column}=? WHERE id=?",(value,self.uid))
            self.assertEqual(client.get("/login/mfa").location,"/login")
            with A.db() as con: con.execute(f"UPDATE users SET {column}=? WHERE id=?",(old,self.uid))
        client=self.pending()
        with A.db() as con: con.execute("UPDATE user_mfa SET security_version=security_version+1 WHERE user_id=?",(self.uid,))
        self.assertEqual(client.get("/login/mfa").location,"/login")

    def test_server_proof_cannot_be_replaced_by_cookie_claims(self):
        # Even a test-created signed cookie claiming MFA is not server verification.
        with self.client.session_transaction() as session: session.update(mfa_verified=True)
        with A.db() as con: con.execute("UPDATE users SET role='ADMIN' WHERE id=?",(self.uid,))
        self.assertEqual(self.client.get("/admin").location,"/login")

    def test_legacy_session_missing_proof_is_rejected(self):
        with self.client.session_transaction() as session: sid=session["sid"]
        with A.db() as con: con.execute("DELETE FROM mfa_session_proofs WHERE token_hash=?",(A.security.opaque(sid),))
        self.assertEqual(self.client.get("/search").location,"/login")

    def test_start_session_refuses_password_only_admin(self):
        with A.db() as con:
            con.execute("UPDATE users SET role='ADMIN' WHERE id=?",(self.uid,))
            user=con.execute("SELECT * FROM users WHERE id=?",(self.uid,)).fetchone()
        with A.app.test_request_context():
            with self.assertRaises(A.mfa.MFAChallengeError): A.start_session(user)

    def test_wrong_encryption_key_and_database_errors_fail_closed(self):
        self.enroll(); client=self.pending()
        with patch.dict(A.app.extensions,mfa_cipher=state["Fernet"](state["Fernet"].generate_key())):
            result=self.verify(client,self.code)
            self.assertEqual(result.status_code,503)
        with patch.object(A.mfa,"verify_login",side_effect=A.sqlite3.OperationalError("synthetic sensitive details")):
            result=self.verify(client,self.codes[0],"recovery")
            self.assertEqual(result.status_code,503)
            self.assertNotIn("synthetic sensitive details",result.get_data(as_text=True))
        self.assertEqual(client.get("/search").location,"/login/mfa")

    def test_pending_challenges_bounded_and_not_sensitive_in_cookie_or_logs(self):
        self.enroll()
        clients=[self.pending() for _ in range(5)]
        with A.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM mfa_challenges WHERE user_id=?",(self.uid,)).fetchone()[0],3)
        self.assertEqual(clients[0].get("/login/mfa").location,"/login")
        result=clients[-1].get("/login/mfa")
        self.assertEqual(result.headers["Cache-Control"],"no-store")
        for value in [self.secret,*self.codes]:
            self.assertNotIn(value,result.get_data(as_text=True))
            self.assertNotIn(value,result.headers.get("Set-Cookie",""))
            self.assertNotIn(value,(TMP/"app.log").read_text(encoding="utf-8"))

    def test_replay_controls_across_concurrent_workers(self):
        self.enroll()
        for method,code in (("totp",self.code),("recovery",self.codes[0])):
            tokens=[self.pending_token(self.pending()) for _ in range(2)]
            def finish(token):
                try:
                    with A.db() as con:
                        con.execute("BEGIN IMMEDIATE")
                        A.mfa.verify_login(con,A.app.extensions["mfa_cipher"],token,code,method,stamp=self.stamp)
                    return True
                except A.mfa.MFAChallengeError: return False
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                self.assertEqual(sorted(executor.map(finish,tokens)),[False,True])

    def test_restart_and_separate_process_share_consumed_state(self):
        self.enroll()
        script="""import sqlite3,sys
from utils import mfa
try:
    with sqlite3.connect(sys.argv[1]) as con:
        con.execute('BEGIN IMMEDIATE')
        mfa.verify_login(con,mfa.configured_cipher(),sys.argv[2],sys.argv[3],sys.argv[5],stamp=float(sys.argv[4]))
    print('verified')
except mfa.MFAChallengeError:
    print('blocked')
"""
        for method,code in (("totp",self.code),("recovery",self.codes[0])):
            for expected in ("verified","blocked"):
                token=self.pending_token(self.pending())
                result=subprocess.run([sys.executable,"-c",script,str(A.DB),token,code,str(self.stamp),method],cwd=TMP,env=dict(os.environ),capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),expected)

    def test_admin_password_change_preserves_verified_current_session(self):
        self.enroll()
        with A.db() as con: con.execute("UPDATE users SET role='ADMIN' WHERE id=?",(self.uid,))
        client=self.pending()
        self.assertEqual(self.verify(client,self.codes[0],"recovery").status_code,302)
        stale=self.pending()
        result=client.post("/account/password",data={"current_password":self.password,"new_password":"Synthetic-New-Admin-Password!","confirm_password":"Synthetic-New-Admin-Password!","csrf_token":csrf(client,"/account/password")})
        self.assertEqual(result.status_code,302)
        self.assertEqual(client.get("/admin").status_code,200)
        self.assertEqual(stale.get("/login/mfa").location,"/login")
        with client.session_transaction() as session: self.assertTrue(session["mfa_verified"])

    def test_admin_disable_reenable_does_not_revive_pending_token(self):
        self.enroll()
        with A.db() as con:
            con.execute("UPDATE users SET role='ADMIN' WHERE id=?",(self.uid,))
        admin=self.pending(); self.verify(admin,self.codes[0],"recovery")
        with A.db() as con:
            con.execute("INSERT INTO users(username,password_hash,role,status) VALUES(?,?,?,?)",("pendingtarget",A.generate_password_hash(self.password),"USER","ACTIVE"))
            target=con.execute("SELECT id FROM users WHERE username='pendingtarget'").fetchone()[0]
            con.execute("INSERT INTO user_mfa(user_id,required) VALUES(?,1)",(target,))
        other=A.app.test_client()
        other.post("/login",data={"username":"pendingtarget","password":self.password,"csrf_token":csrf(other,"/login")})
        saved=self.pending_token(other)
        for status in ("DISABLED","ACTIVE"):
            result=admin.post(f"/admin/users/{target}",data={"role":"USER","status":status,"csrf_token":csrf(admin,f"/admin/users/{target}")})
            self.assertEqual(result.status_code,302)
        with other.session_transaction() as session: session["mfa_pending"]=saved
        self.assertEqual(other.get("/account/mfa").location,"/login")


if __name__=="__main__": unittest.main()
