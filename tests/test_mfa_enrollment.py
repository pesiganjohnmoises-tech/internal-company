"""Enrollment checks on a disposable app copy; no live files or deployment calls."""
import concurrent.futures
import importlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from cryptography.fernet import Fernet
import pyotp

SRC=Path(__file__).resolve().parent.parent
TMP=Path(tempfile.mkdtemp(prefix="directory-mfa-enrollment-test-"))
for name in ("app.py","utils","templates","static"):
    source=SRC/name
    if source.is_dir(): shutil.copytree(source,TMP/name,ignore=shutil.ignore_patterns("__pycache__"))
    else: shutil.copy2(source,TMP/name)
os.environ.update(SECRET_KEY="synthetic-mfa-test-session-key",ADMIN_PASSWORD="Synthetic-Admin-Password!",
                  DEPLOY_SECRET="",COOKIE_SECURE="0",APP_ENV="development",CLIENT_IP_HEADER="",TRUSTED_PROXY_CIDRS="",
                  MFA_ENABLED="0",MFA_ENROLLMENT_ENABLED="0",MFA_ENCRYPTION_KEY=Fernet.generate_key().decode(),MFA_ENCRYPTION_KEY_FILE="")
os.chdir(TMP); sys.path.insert(0,str(TMP))
A=importlib.import_module("app")
A.mfa=importlib.import_module("utils.mfa")
A.app.config.update(TESTING=True,WTF_CSRF_ENABLED=True)
with A.db() as con: A.mfa.migrate(con)
A.app.config.update(MFA_ENABLED=True,MFA_ENROLLMENT_ENABLED=True)
A.app.extensions["mfa_cipher"]=A.mfa.configured_cipher()


def csrf(client,path="/account/mfa"):
    response=client.get(path)
    return re.search(r'name="csrf_token" value="([^"]+)"',response.get_data(as_text=True)).group(1)


class EnrollmentTests(unittest.TestCase):
    serial=0

    def test_local_marker_enables_direct_run_only_and_respects_environment(self):
        folder=Path(tempfile.mkdtemp(prefix="directory-local-mfa-startup-"))
        for name in ("app.py","utils","templates","static"):
            source=TMP/name
            if source.is_dir(): shutil.copytree(source,folder/name,ignore=shutil.ignore_patterns("__pycache__"))
            else: shutil.copy2(source,folder/name)
        env=dict(os.environ); env.update(MFA_ENABLED="0",MFA_ENCRYPTION_KEY="",MFA_ENCRYPTION_KEY_FILE="")
        subprocess.run([sys.executable,"-c","import app"],cwd=folder,env=env,check=True,capture_output=True)
        key=Fernet.generate_key().decode()
        (folder/".mfa_key").write_text(key)
        migration_env={**env,"MFA_ENCRYPTION_KEY_FILE":str(folder/".mfa_key")}
        result=subprocess.run([sys.executable,"-m","utils.mfa","migrate","--database",str(folder/"directory.db"),"--apply","--backup",str(folder/"before.db")],cwd=folder,env=migration_env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        (folder/".mfa-local-enabled").write_text("Local MFA enabled\n")
        script="import runpy; from flask import Flask; Flask.run=lambda self,**kw: print('enabled='+str(self.config['MFA_ENABLED'])); runpy.run_path('app.py',run_name='__main__')"
        enabled_env=dict(env); enabled_env.pop("MFA_ENABLED")
        result=subprocess.run([sys.executable,"-c",script],cwd=folder,env=enabled_env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr); self.assertIn("enabled=True",result.stdout)
        for code,values in ((script,env),("import app; print('enabled='+str(app.app.config['MFA_ENABLED']))",enabled_env),
                            (script,{**enabled_env,"APP_ENV":"production","SECRET_KEY":"S"*40,"COOKIE_SECURE":"1"})):
            result=subprocess.run([sys.executable,"-c",code],cwd=folder,env=values,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr); self.assertIn("enabled=False",result.stdout)

    def setUp(self):
        type(self).serial+=1
        self.name="synthetic"+str(self.serial)
        self.password="Synthetic-User-Password!"
        with A.db() as con:
            con.execute("DELETE FROM security_events")
            con.execute("INSERT INTO users(username,password_hash,role,status) VALUES(?,?,?,?)",
                        (self.name,A.generate_password_hash(self.password),"USER","ACTIVE"))
            self.uid=con.execute("SELECT id FROM users WHERE username=?",(self.name,)).fetchone()[0]
        self.client=self.login()

    def login(self):
        client=A.app.test_client()
        response=client.post("/login",data={"username":self.name,"password":self.password,"csrf_token":csrf(client,"/login")})
        self.assertEqual(response.status_code,302)
        return client

    def post(self,action,**fields):
        return self.client.post("/account/mfa",data={"action":action,"csrf_token":csrf(self.client),**fields})

    def start(self):
        self.assertEqual(self.post("start",current_password=self.password).status_code,302)
        html=self.client.get("/account/mfa").get_data(as_text=True)
        secret=re.search(r'<code class="mfa-key">([A-Z2-7]{32})</code>',html).group(1)
        with self.client.session_transaction() as session:
            self.challenge=session["mfa_enrollment"]; self.sid=session["sid"]
            self.assertNotIn(secret,str(dict(session)))
        return secret

    def confirm(self,secret):
        return self.post("confirm",code=pyotp.TOTP(secret).now())

    def test_password_required_before_secret_or_qr(self):
        self.assertNotIn('class="mfa-key"',self.client.get("/account/mfa").get_data(as_text=True))
        self.assertEqual(self.client.get("/account/mfa/qr").status_code,404)
        result=self.post("start",current_password="wrong")
        self.assertIn("incorrect",result.get_data(as_text=True))
        self.assertEqual(self.client.get("/account/mfa/qr").status_code,404)

    def test_csrf_rejects_every_mutating_action(self):
        for action in ("start","confirm","cancel"):
            for value in (None,"invalid"):
                data={"action":action,"current_password":self.password,"code":"123456"}
                if value: data["csrf_token"]=value
                self.assertEqual(self.client.post("/account/mfa",data=data).status_code,400)
        with A.db() as con:
            self.assertIsNone(con.execute("SELECT user_id FROM user_mfa WHERE user_id=?",(self.uid,)).fetchone())

    def test_qr_uses_same_secret_and_is_private_svg(self):
        secret=self.start()
        with patch.object(A.mfa.qrcode,"make",wraps=A.mfa.qrcode.make) as generator:
            result=self.client.get("/account/mfa/qr")
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.mimetype,"image/svg+xml")
        self.assertTrue(ElementTree.fromstring(result.data).tag.endswith("svg"))
        provisioned=pyotp.parse_uri(generator.call_args.args[0])
        self.assertEqual(provisioned.secret,secret)
        self.assertEqual(provisioned.name,self.name)
        self.assertEqual(result.headers["Cache-Control"],"no-store")
        self.assertNotIn(secret,result.headers.get("Set-Cookie",""))
        self.assertNotIn("otpauth",result.data.decode())

    def test_pending_secret_is_encrypted_and_bound_to_browser(self):
        secret=self.start()
        other=self.login()
        with other.session_transaction() as session: session["mfa_enrollment"]=self.challenge
        self.assertEqual(other.get("/account/mfa/qr").status_code,404)
        with A.db() as con:
            row=con.execute("SELECT * FROM mfa_challenges WHERE user_id=?",(self.uid,)).fetchone()
            self.assertNotIn(secret,str(tuple(row)))
            self.assertNotIn(self.challenge,str(tuple(row)))
            self.assertEqual(A.mfa.decrypt_secret(A.app.extensions["mfa_cipher"],self.uid,row["encrypted_pending_secret"]),secret)

    def test_cross_account_challenge_cannot_be_read(self):
        self.start()
        with A.db() as con: con.execute("INSERT INTO users(username,password_hash,role,status) VALUES(?,?,?,?)",("another",A.generate_password_hash(self.password),"USER","ACTIVE"))
        other=A.app.test_client()
        other.post("/login",data={"username":"another","password":self.password,"csrf_token":csrf(other,"/login")})
        with other.session_transaction() as session: session["mfa_enrollment"]=self.challenge
        self.assertEqual(other.get("/account/mfa/qr").status_code,404)

    def test_expiry_prevents_qr_and_activation(self):
        secret=self.start()
        with A.db() as con: con.execute("UPDATE mfa_challenges SET created_at=?,expires_at=? WHERE user_id=?",(time.time()-700,time.time()-1,self.uid))
        self.assertEqual(self.client.get("/account/mfa/qr").status_code,404)
        result=self.confirm(secret)
        self.assertEqual(result.status_code,302)
        with A.db() as con: self.assertIsNone(con.execute("SELECT enabled_at FROM user_mfa WHERE user_id=?",(self.uid,)).fetchone()[0])

    def test_wrong_and_malformed_codes_do_not_activate(self):
        secret=self.start()
        bad=next(f"{value:06d}" for value in range(10) if A.mfa.matching_step(secret,f"{value:06d}") is None)
        for code in (bad,"abcdef","12345","１２３４５６"):
            self.assertEqual(self.post("confirm",code=code).status_code,302)
        with A.db() as con: self.assertIsNone(con.execute("SELECT encrypted_secret FROM user_mfa WHERE user_id=?",(self.uid,)).fetchone()[0])
        self.assertEqual(self.post("confirm",code="x"*100).status_code,400)

    def test_code_attempts_are_limited_per_account(self):
        secret=self.start()
        bad=next(f"{value:06d}" for value in range(10) if A.mfa.matching_step(secret,f"{value:06d}") is None)
        for _ in range(5): self.assertEqual(self.post("confirm",code=bad).status_code,302)
        result=self.confirm(secret)
        self.assertEqual(result.status_code,429)
        self.assertIn("Retry-After",result.headers)

    def test_cancel_and_restart_invalidate_old_challenge(self):
        secret=self.start(); old=self.challenge
        self.assertEqual(self.post("cancel").status_code,302)
        self.assertEqual(self.client.get("/account/mfa/qr").status_code,404)
        self.assertNotEqual(self.start(),secret)
        self.assertNotEqual(old,self.challenge)
        with A.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM mfa_challenges WHERE user_id=?",(self.uid,)).fetchone()[0],1)

    def test_policy_change_invalidates_enrollment(self):
        self.start()
        with A.db() as con: con.execute("UPDATE user_mfa SET security_version=security_version+1 WHERE user_id=?",(self.uid,))
        result=self.client.get("/account/mfa/qr")
        self.assertEqual(result.status_code,302)
        self.assertEqual(result.location,"/login")

    def test_password_change_invalidates_enrollment(self):
        self.start()
        with A.db() as con: con.execute("UPDATE users SET password_hash=? WHERE id=?",(A.generate_password_hash("Synthetic-New-Password!"),self.uid))
        self.assertEqual(self.client.get("/account/mfa/qr").status_code,302)

    def test_session_revocation_between_requests_cannot_finish_setup(self):
        secret=self.start()
        with A.db() as con:
            con.execute("DELETE FROM security_sessions WHERE user_id=?",(self.uid,))
            with self.assertRaises(A.mfa.MFAEnrollmentError):
                A.mfa.complete_enrollment(con,A.app.extensions["mfa_cipher"],self.uid,self.challenge,self.sid,pyotp.TOTP(secret).now())

    def test_recovery_codes_shown_once_only_hashes_stored_and_sessions_revoked(self):
        other=self.login(); secret=self.start()
        result=self.confirm(secret)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.headers["Cache-Control"],"no-store")
        codes=re.findall(r"<code>((?:[A-F0-9]{4}-){4}[A-F0-9]{4})</code>",result.get_data(as_text=True))
        self.assertEqual(len(codes),10); self.assertEqual(len(set(codes)),10)
        with A.db() as con:
            row=con.execute("SELECT * FROM user_mfa WHERE user_id=?",(self.uid,)).fetchone()
            self.assertEqual(A.mfa.decrypt_secret(A.app.extensions["mfa_cipher"],self.uid,row["encrypted_secret"]),secret)
            self.assertEqual(row["security_version"],1)
            self.assertIsNone(A.mfa.matching_step(secret,pyotp.TOTP(secret).at(row["last_used_step"]*30),stamp=row["last_used_step"]*30,last_used_step=row["last_used_step"]))
            stored={r[0] for r in con.execute("SELECT code_hash FROM mfa_recovery_codes WHERE user_id=?",(self.uid,))}
            self.assertEqual(stored,{A.mfa.recovery_hash(code) for code in codes})
            self.assertEqual(con.execute("SELECT COUNT(*) FROM mfa_challenges WHERE user_id=?",(self.uid,)).fetchone()[0],0)
            dumped="\n".join(con.iterdump())
            self.assertNotIn(secret,dumped)
            for code in codes: self.assertNotIn(code,dumped)
        with self.client.session_transaction() as session:
            for value in [secret,*codes]: self.assertNotIn(value,str(dict(session)))
        self.assertEqual(other.get("/search").status_code,302)
        page=self.client.get("/account/mfa").get_data(as_text=True)
        self.assertIn("authenticator is enrolled",page)
        for value in [secret,*codes]: self.assertNotIn(value,page)
        self.assertEqual(self.client.get("/account/mfa/qr").status_code,404)
        self.assertEqual(self.confirm(secret).status_code,302)
        log=(TMP/"app.log").read_text(encoding="utf-8")
        for value in [secret,*codes]: self.assertNotIn(value,log)

    def test_concurrent_completion_has_one_winner(self):
        secret=self.start(); code=pyotp.TOTP(secret).now()
        def finish():
            try:
                with A.db() as con:
                    con.execute("BEGIN IMMEDIATE")
                    A.mfa.complete_enrollment(con,A.app.extensions["mfa_cipher"],self.uid,self.challenge,self.sid,code)
                return True
            except A.mfa.MFAEnrollmentError: return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(lambda _:finish(),range(2))),[False,True])

    def test_existing_enrollment_cannot_be_replaced(self):
        secret=self.start(); self.confirm(secret)
        self.assertEqual(self.post("start",current_password=self.password).status_code,302)
        with A.db() as con:
            encrypted=con.execute("SELECT encrypted_secret FROM user_mfa WHERE user_id=?",(self.uid,)).fetchone()[0]
            self.assertEqual(A.mfa.decrypt_secret(A.app.extensions["mfa_cipher"],self.uid,encrypted),secret)

    def test_configuration_off_hides_ui_and_endpoints(self):
        with patch.dict(A.app.config,MFA_ENROLLMENT_ENABLED=False):
            self.assertEqual(self.client.get("/account/mfa").status_code,404)
            self.assertEqual(self.client.get("/account/mfa/qr").status_code,404)
            self.assertNotIn("Account security",self.client.get("/search").get_data(as_text=True))

    def test_anonymous_access_and_admin_permissions_unchanged(self):
        anonymous=A.app.test_client()
        self.assertEqual(anonymous.get("/account/mfa").status_code,302)
        self.assertEqual(anonymous.get("/account/mfa/qr").status_code,302)
        self.assertEqual(self.client.get("/admin").status_code,403)
        self.assertEqual(self.client.get("/account/password").status_code,403)

    def test_storage_failures_return_generic_errors(self):
        self.start()
        with patch.object(A.mfa,"pending_enrollment",side_effect=A.mfa.MFASecretError("synthetic-secret")):
            result=self.client.get("/account/mfa/qr")
            self.assertEqual(result.status_code,503)
            self.assertNotIn("synthetic-secret",result.get_data(as_text=True))

    def test_expired_csrf_and_admin_enrollment(self):
        token=csrf(self.client)
        with patch.dict(A.app.config,WTF_CSRF_TIME_LIMIT=-1):
            self.assertEqual(self.client.post("/account/mfa",data={"action":"start","current_password":self.password,"csrf_token":token}).status_code,400)
        with A.db() as con: con.execute("UPDATE users SET role='ADMIN' WHERE id=?",(self.uid,))
        # A promoted admin's old password-only session is now rejected.
        response=self.client.post("/login",data={"username":self.name,"password":self.password,"csrf_token":csrf(self.client,"/login")})
        self.assertEqual(response.location,"/account/mfa")
        secret=re.search(r'<code class="mfa-key">([A-Z2-7]{32})</code>',self.client.get("/account/mfa").get_data(as_text=True)).group(1)
        self.assertEqual(self.confirm(secret).status_code,200)

    def test_ip_limit_and_wrong_password_limits(self):
        for _ in range(5): self.post("start",current_password="wrong")
        self.assertEqual(self.post("start",current_password=self.password).status_code,429)
        with A.db() as con: con.execute("DELETE FROM security_events")
        secret=self.start()
        for _ in range(30): A.security.reserve(A.DB,[("mfa-ip","127.0.0.1",30,900)])
        self.assertEqual(self.confirm(secret).status_code,429)

    def test_preview_rejects_production_and_invalid_key_at_startup(self):
        for changes in ({"APP_ENV":"production","SECRET_KEY":"synthetic-production-session-key-32-chars","COOKIE_SECURE":"1"},
                        {"MFA_ENCRYPTION_KEY":""},{"MFA_ENCRYPTION_KEY":"invalid"}):
            env={**os.environ,"MFA_ENABLED":"1",**changes}
            result=subprocess.run([sys.executable,"-c","import app"],cwd=TMP,env=env,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn(os.environ["MFA_ENCRYPTION_KEY"],result.stdout+result.stderr)

    def test_totp_window_and_recovery_normalization(self):
        secret=pyotp.random_base32(); stamp=2_000_000_010
        current=stamp//30
        for step in (current-1,current,current+1):
            self.assertEqual(A.mfa.matching_step(secret,pyotp.TOTP(secret).at(step*30),stamp),step)
        self.assertIsNone(A.mfa.matching_step(secret,pyotp.TOTP(secret).at((current-3)*30),stamp))
        code="ABCD-1234-ABCD-5678-ABCD"
        self.assertEqual(A.mfa.recovery_hash(code),A.mfa.recovery_hash(code.lower()))


if __name__=="__main__": unittest.main()
