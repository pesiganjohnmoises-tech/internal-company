"""Factor management and recovery on disposable copies and synthetic data only."""
from pathlib import Path
import concurrent.futures
import os
import re
import runpy
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

state=runpy.run_path(str(Path(__file__).resolve().parent/"test_mfa_login.py"))
A=state["A"]; csrf=state["csrf"]; TMP=state["TMP"]
base=state["MFALoginTests"]


class ManagementTests(unittest.TestCase):
    serial=0
    setUp=base.setUp
    login=base.login
    post=base.post
    start=base.start
    confirm=base.confirm
    enroll=base.enroll
    verify=base.verify

    def manage(self,action,code=None,method="recovery",password=None):
        return self.client.post("/account/mfa/manage",data={"action":action,"current_password":self.password if password is None else password,
            "method":method,"code":self.codes[0] if code is None else code,"csrf_token":csrf(self.client)})

    def row(self):
        with A.db() as con: return dict(con.execute("SELECT * FROM user_mfa WHERE user_id=?",(self.uid,)).fetchone())

    def codes_from(self,response):
        return re.findall(r"<code>((?:[A-F0-9]{4}-){4}[A-F0-9]{4})</code>",response.get_data(as_text=True))

    def test_replacement_keeps_old_factor_until_first_new_code(self):
        self.enroll(); before=self.row(); other=self.login()
        self.assertEqual(self.verify(other,self.codes[1],"recovery").status_code,302)
        self.assertEqual(self.manage("replace").status_code,302)
        self.assertEqual(self.row()["encrypted_secret"],before["encrypted_secret"])
        html=self.client.get("/account/mfa").get_data(as_text=True)
        replacement=re.search(r'<code class="mfa-key">([A-Z2-7]{32})</code>',html).group(1)
        self.assertNotEqual(replacement,self.secret)
        self.assertEqual(self.client.get("/account/mfa/qr").status_code,200)
        result=self.confirm(replacement)
        self.assertEqual(result.status_code,200)
        newcodes=self.codes_from(result); self.assertEqual(len(newcodes),10)
        self.assertNotEqual(self.row()["encrypted_secret"],before["encrypted_secret"])
        self.assertEqual(other.get("/search").location,"/login")
        self.assertEqual(self.client.get("/search").status_code,200)
        pending=self.login(); self.assertEqual(self.verify(pending,self.codes[2],"recovery").status_code,200)
        self.assertEqual(self.verify(pending,newcodes[0],"recovery").status_code,302)

    def test_cancel_and_expired_replacement_preserve_factor(self):
        self.enroll(); before=self.row()
        self.manage("replace"); self.post("cancel")
        self.assertEqual(self.row()["encrypted_secret"],before["encrypted_secret"])
        self.assertEqual(self.client.get("/account/mfa/qr").status_code,404)
        self.manage("replace",self.codes[1])
        with A.db() as con: con.execute("UPDATE mfa_challenges SET created_at=?,expires_at=? WHERE user_id=?",(time.time()-1000,time.time()-1,self.uid))
        self.assertNotIn('class="mfa-key"',self.client.get("/account/mfa").get_data(as_text=True))
        self.assertEqual(self.row()["encrypted_secret"],before["encrypted_secret"])

    def test_regeneration_replaces_hashes_and_revokes_sessions(self):
        self.enroll(); other=self.login(); self.verify(other,self.codes[1],"recovery")
        result=self.manage("regenerate"); new=self.codes_from(result)
        self.assertEqual(len(new),10); self.assertTrue(set(new).isdisjoint(self.codes))
        self.assertEqual(result.headers["Cache-Control"],"no-store")
        self.assertEqual(other.get("/search").location,"/login")
        self.assertEqual(self.client.get("/search").status_code,200)
        with A.db() as con:
            hashes=con.execute("SELECT code_hash FROM mfa_recovery_codes WHERE user_id=?",(self.uid,)).fetchall()
            self.assertEqual(len(hashes),10); self.assertNotIn(new[0],str(hashes))
        pending=self.login(); self.assertEqual(self.verify(pending,self.codes[2],"recovery").status_code,200)
        self.assertEqual(self.verify(pending,new[0],"recovery").status_code,302)

    def test_optional_disable_revokes_codes_and_returns_password_only(self):
        self.enroll(); other=self.login(); self.verify(other,self.codes[1],"recovery")
        self.assertEqual(self.manage("disable").status_code,302)
        self.assertIsNone(self.row()["encrypted_secret"])
        self.assertEqual(other.get("/search").location,"/login")
        self.assertEqual(self.login().get("/search").status_code,200)
        with A.db() as con: self.assertEqual(con.execute("SELECT COUNT(*) FROM mfa_recovery_codes WHERE user_id=?",(self.uid,)).fetchone()[0],0)

    def test_required_disable_is_denied_and_hidden(self):
        self.enroll()
        with A.db() as con: con.execute("UPDATE user_mfa SET required=1 WHERE user_id=?",(self.uid,))
        before=self.row(); self.manage("disable")
        self.assertEqual(self.row(),before)
        self.assertNotIn("Disable optional MFA",self.client.get("/account/mfa").get_data(as_text=True))

    def test_wrong_password_code_csrf_and_replay_do_not_change_factor(self):
        self.enroll(); before=self.row()
        self.manage("disable",password="wrong")
        self.manage("disable",code="0000-0000-0000-0000-0000")
        self.assertEqual(self.client.post("/account/mfa/manage",data={"action":"disable"}).status_code,400)
        self.assertEqual(self.row(),before)
        self.manage("replace")
        self.assertEqual(self.manage("disable").status_code,302)
        self.assertIsNotNone(self.row()["encrypted_secret"])

    def test_shared_totp_replay_and_failure_limit(self):
        self.enroll()
        self.manage("replace",self.code,"totp")
        self.manage("disable",self.code,"totp")
        self.assertIsNotNone(self.row()["encrypted_secret"])
        for _ in range(4): self.manage("disable",code="0000-0000-0000-0000-0000")
        self.assertEqual(self.manage("disable",self.codes[1]).status_code,429)

    def make_admin(self):
        name="managementadmin"+str(self.serial)
        with A.db() as con:
            con.execute("INSERT INTO users(username,password_hash,role,status) VALUES(?,?,?,?)",(name,A.generate_password_hash(self.password),"ADMIN","ACTIVE"))
            uid=con.execute("SELECT id FROM users WHERE username=?",(name,)).fetchone()[0]
        admin=A.app.test_client(); admin.post("/login",data={"username":name,"password":self.password,"csrf_token":csrf(admin,"/login")})
        secret=re.search(r'<code class="mfa-key">([A-Z2-7]{32})</code>',admin.get("/account/mfa").get_data(as_text=True)).group(1)
        result=admin.post("/account/mfa",data={"action":"confirm","code":state["pyotp"].TOTP(secret).now(),"csrf_token":csrf(admin)})
        return admin,uid,self.codes_from(result)

    def reset(self,admin,code,uid=None,**extra):
        uid=self.uid if uid is None else uid
        return admin.post(f"/admin/users/{uid}/mfa-reset",data={"current_password":self.password,"method":"recovery","code":code,
            "confirm_user_id":str(uid),"identity_verified":"1","csrf_token":csrf(admin,f"/admin/users/{uid}"),**extra})

    def test_admin_reset_preserves_policy_and_requires_enrollment(self):
        self.enroll(); before=self.row(); admin,_,codes=self.make_admin()
        with A.db() as con: identity=tuple(con.execute("SELECT * FROM users WHERE id=?",(self.uid,)).fetchone())
        self.assertEqual(self.reset(admin,codes[0]).status_code,302)
        after=self.row(); self.assertEqual(after["required"],before["required"])
        self.assertEqual(after["recovery_required"],1); self.assertIsNone(after["encrypted_secret"])
        self.assertEqual(self.client.get("/search").location,"/login")
        with A.db() as con: self.assertEqual(tuple(con.execute("SELECT * FROM users WHERE id=?",(self.uid,)).fetchone()),identity)
        admin.post(f"/admin/users/{self.uid}/mfa-policy",data={"required":"0","csrf_token":csrf(admin,f"/admin/users/{self.uid}")})
        pending=self.login(); self.assertEqual(pending.get("/search").location,"/login/mfa")
        secret=re.search(r'<code class="mfa-key">([A-Z2-7]{32})</code>',pending.get("/account/mfa").get_data(as_text=True)).group(1)
        result=pending.post("/account/mfa",data={"action":"confirm","code":state["pyotp"].TOTP(secret).now(),"csrf_token":csrf(pending)})
        self.assertEqual(result.status_code,200); self.assertEqual(self.row()["recovery_required"],0)
        self.assertEqual(pending.get("/search").status_code,200)

    def test_admin_reset_needs_identity_password_factor_and_rejects_self(self):
        self.enroll(); admin,uid,codes=self.make_admin(); before=self.row()
        self.assertEqual(self.reset(admin,codes[0],identity_verified="0").status_code,400)
        self.assertEqual(self.reset(admin,codes[0],confirm_user_id="999").status_code,400)
        self.assertEqual(self.reset(admin,codes[0],uid=uid).status_code,400)
        self.reset(admin,codes[0],current_password="wrong")
        self.assertEqual(self.row(),before)
        self.assertEqual(self.reset(self.client,self.codes[0]).status_code,403)
        self.assertNotIn('mfa-reset',admin.get(f"/admin/users/{uid}").get_data(as_text=True))

    def test_management_rollback_and_flag_off(self):
        self.enroll(); before=self.row()
        with patch.object(A.mfa,"regenerate_recovery",side_effect=sqlite3.OperationalError("synthetic failure")):
            self.assertEqual(self.manage("regenerate").status_code,503)
        self.assertEqual(self.row(),before)
        with A.db() as con: self.assertIsNone(con.execute("SELECT used_at FROM mfa_recovery_codes WHERE user_id=? AND code_hash=?",(self.uid,A.mfa.recovery_hash(self.codes[0]))).fetchone()[0])
        with patch.dict(A.app.config,MFA_ENABLED=False,MFA_ENROLLMENT_ENABLED=False): self.assertEqual(self.manage("disable").status_code,404)

    def test_admin_cannot_disable_mfa_even_with_fresh_recovery_code(self):
        admin,uid,codes=self.make_admin()
        response=admin.post("/account/mfa/manage",data={"action":"disable","current_password":self.password,"method":"recovery","code":codes[0],"csrf_token":csrf(admin)})
        self.assertEqual(response.status_code,302)
        with A.db() as con:
            user=dict(con.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone())
            self.assertTrue(A.mfa.account_state(con,user)["enabled"])
        self.assertEqual(admin.get("/admin").status_code,200)

    def test_reset_preserves_required_policy_grants_and_revokes_pending_logins(self):
        self.enroll(); pending=self.login(); admin,_,codes=self.make_admin()
        with A.db() as con:
            con.execute("UPDATE user_mfa SET required=1 WHERE user_id=?",(self.uid,))
            con.execute("INSERT INTO user_field_access VALUES(?,?)",(self.uid,"synthetic-column"))
        self.reset(admin,codes[0])
        self.assertEqual(self.row()["required"],1)
        self.assertEqual(pending.get("/login/mfa").location,"/login")
        with A.db() as con: self.assertEqual(con.execute("SELECT field_name FROM user_field_access WHERE user_id=?",(self.uid,)).fetchone()[0],"synthetic-column")

    def test_management_and_reset_log_only_safe_identifiers(self):
        self.enroll(); admin,_,codes=self.make_admin()
        with self.assertLogs(level="INFO") as captured:
            self.manage("replace")
            self.reset(admin,codes[0])
        output=str(captured.output)
        self.assertIn("actor_id=",output); self.assertIn("target_id=",output)
        for secret in (self.password,self.secret,self.codes[0],codes[0]): self.assertNotIn(secret,output)

    def test_concurrent_same_recovery_code_only_one_consumes(self):
        self.enroll()
        with self.client.session_transaction() as s: sid=s["sid"]
        with A.db() as con: user=dict(con.execute("SELECT * FROM users WHERE id=?",(self.uid,)).fetchone())
        def consume():
            try:
                with A.db() as con:
                    con.execute("BEGIN IMMEDIATE")
                    A.mfa.verify_management(con,A.app.extensions["mfa_cipher"],self.uid,sid,user["password_hash"],self.codes[0],"recovery","USER")
                return True
            except A.mfa.MFAChallengeError: return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool: self.assertEqual(sorted(pool.map(lambda _:consume(),range(2))),[False,True])

    def test_emergency_console_preview_confirmations_and_backup(self):
        admin,uid,codes=self.make_admin()
        def cli(*args):
            return subprocess.run([sys.executable,"-m","utils.mfa","recover-admin","--database",str(A.DB),"--user-id",str(uid),*args],cwd=TMP,env=dict(os.environ),capture_output=True,text=True)
        self.assertEqual(cli().returncode,0)
        self.assertEqual(admin.get("/admin").status_code,200)
        backup=TMP/("emergency-backup-"+str(self.serial)+".sqlite")
        self.assertNotEqual(cli("--apply","--backup",str(backup)).returncode,0); self.assertFalse(backup.exists())
        result=cli("--apply","--backup",str(backup),"--confirm-user-id",str(uid),"--identity-verified")
        self.assertEqual(result.returncode,0,result.stderr); self.assertTrue(backup.exists())
        self.assertIn("actor=hosting-console",result.stdout); self.assertNotIn(codes[0],result.stdout)
        self.assertEqual(admin.get("/admin").location,"/login")
        with A.db() as con:
            user=dict(con.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone())
            flags=A.mfa.account_state(con,user)
            self.assertTrue(flags["required"]); self.assertTrue(flags["recovery_required"]); self.assertFalse(flags["enabled"])


del base
if __name__=="__main__": unittest.main()
