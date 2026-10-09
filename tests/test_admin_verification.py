"""Fresh admin verification on disposable code copies and synthetic SQLite only."""
from pathlib import Path
import re, runpy, unittest
from unittest.mock import patch
state=runpy.run_path(str(Path(__file__).resolve().parent/"test_mfa_enrollment.py"))
A=state["A"]; csrf=state["csrf"]; TMP=state["TMP"]

class AdminVerificationTests(unittest.TestCase):
    serial=0
    def setUp(self):
        type(self).serial+=1
        self.password="Synthetic-Verification-Password!"; self.name="auditadmin"+str(self.serial)
        with A.db() as c:
            c.execute("DELETE FROM security_events")
            self.actor=c.execute("INSERT INTO users(username,password_hash,role,status) VALUES(?,?,?,?)",(self.name,A.generate_password_hash(self.password),"ADMIN","ACTIVE")).lastrowid
            self.target=c.execute("INSERT INTO users(username,password_hash,role,status) VALUES(?,?,?,?)",("audittarget"+str(self.serial),A.generate_password_hash("Synthetic-Target-Password!"),"USER","ACTIVE")).lastrowid
        self.client=A.app.test_client()
        self.client.post("/login",data={"username":self.name,"password":self.password,"csrf_token":csrf(self.client,"/login")})
        html=self.client.get("/account/mfa").get_data(as_text=True)
        self.secret=re.search(r'<code class="mfa-key">([A-Z2-7]{32})</code>',html).group(1)
        self.enrollment_code=state["pyotp"].TOTP(self.secret).now()
        result=self.client.post("/account/mfa",data={"action":"confirm","code":self.enrollment_code,"csrf_token":csrf(self.client)})
        self.assertEqual(result.status_code,200)
        self.codes=re.findall(r"<code>((?:[A-F0-9]{4}-){4}[A-F0-9]{4})</code>",result.get_data(as_text=True))
        self.base=f"/admin/users/{self.target}"

    def snapshot(self):
        with A.db() as c:
            return {table:[tuple(r) for r in c.execute("SELECT * FROM "+table)] for table in
                    ("users","user_country_access","user_company_access","user_field_access","user_mfa","mfa_recovery_codes","mfa_challenges","mfa_session_proofs")}

    def creds(self,index=0): return {"current_password":self.password,"method":"recovery","code":self.codes[index]}
    def post(self,path,data,creds=None):
        return self.client.post(path,data={"csrf_token":csrf(self.client,self.base),**data,**(creds or {})})
    def creation(self): return {"username":"created"+str(self.serial),"password":"Synthetic-New-Password!","role":"ADMIN"}
    def actions(self):
        return [("/admin/users/create",self.creation()),(self.base,{"role":"ADMIN","status":"ACTIVE"}),
                (self.base,{"role":"USER","status":"DISABLED"}),(self.base,{"role":"USER","status":"ACTIVE","password":"Synthetic-New-Password!"}),
                (self.base+"/mfa-policy",{"required":"1"}),(self.base+"/delete",{})]

    def test_missing_verification_blocks_every_sensitive_action(self):
        for path,data in self.actions():
            before=self.snapshot(); self.assertEqual(self.post(path,data).status_code,302); self.assertEqual(before,self.snapshot())
            with A.db() as c: c.execute("DELETE FROM security_events")

    def test_wrong_password_and_factor_save_nothing(self):
        for creds in ({**self.creds(),"current_password":"wrong"},{**self.creds(),"code":"0000-0000-0000-0000-0000"}):
            before=self.snapshot(); self.post("/admin/users/create",self.creation(),creds); self.assertEqual(before,self.snapshot())

    def test_creation_and_recovery_replay(self):
        data=self.creation(); self.post("/admin/users/create",data,self.creds())
        with A.db() as c: self.assertEqual(c.execute("SELECT role FROM users WHERE username=?",(data["username"],)).fetchone()[0],"ADMIN")
        before=self.snapshot(); self.post("/admin/users/create",{**data,"username":"replay"+str(self.serial)},self.creds()); self.assertEqual(before,self.snapshot())

    def test_roles_status_password_and_delete(self):
        for index,(role,status) in enumerate((("ADMIN","ACTIVE"),("USER","DISABLED"),("USER","ACTIVE"))):
            self.post(self.base,{"role":role,"status":status},self.creds(index))
            with A.db() as c: self.assertEqual(tuple(c.execute("SELECT role,status FROM users WHERE id=?",(self.target,)).fetchone()),(role,status))
        self.post(self.base,{"role":"USER","status":"ACTIVE","password":"Synthetic-New-Password!"},self.creds(3))
        with A.db() as c: self.assertTrue(A.check_password_hash(c.execute("SELECT password_hash FROM users WHERE id=?",(self.target,)).fetchone()[0],"Synthetic-New-Password!"))
        self.post(self.base+"/delete",{},self.creds(4))
        with A.db() as c: self.assertIsNone(c.execute("SELECT id FROM users WHERE id=?",(self.target,)).fetchone())

    def test_policy_change_and_noop(self):
        for index,value in enumerate((1,0)):
            self.post(self.base+"/mfa-policy",{"required":str(value)},self.creds(index))
            with A.db() as c: self.assertEqual(c.execute("SELECT required FROM user_mfa WHERE user_id=?",(self.target,)).fetchone()[0],value)
        before=self.snapshot(); self.post(self.base+"/mfa-policy",{"required":"0"}); self.assertEqual(before,self.snapshot())

    def test_ordinary_name_and_grant_edit_needs_no_verification(self):
        with A.db() as c:
            cid=c.execute("INSERT INTO countries(name) VALUES(?)",("auditcountry"+str(self.serial),)).lastrowid
        self.post(self.base,{"role":"USER","status":"ACTIVE","first_name":"Updated","countries":str(cid),"all_companies":str(cid)})
        with A.db() as c:
            self.assertEqual(c.execute("SELECT first_name FROM users WHERE id=?",(self.target,)).fetchone()[0],"Updated")
            self.assertEqual(c.execute("SELECT all_companies FROM user_country_access WHERE user_id=? AND country_id=?",(self.target,cid)).fetchone()[0],1)

    def test_failed_write_rolls_back_factor_and_business_changes(self):
        before=self.snapshot()
        with patch.object(A,"revoke_user_mfa",side_effect=A.sqlite3.OperationalError("synthetic private detail")):
            result=self.post(self.base,{"role":"ADMIN","status":"ACTIVE"},self.creds())
        self.assertEqual(result.status_code,503); self.assertNotIn("synthetic private detail",result.get_data(as_text=True)); self.assertEqual(before,self.snapshot())

    def test_duplicate_creation_does_not_consume_code(self):
        before=self.snapshot(); self.post("/admin/users/create",{**self.creation(),"username":self.name},self.creds()); self.assertEqual(before,self.snapshot())

    def test_session_revoked_between_preflight_and_write(self):
        original=A.prepare_admin_verification
        def revoke(url):
            inputs=original(url)
            with A.db() as c: c.execute("DELETE FROM security_sessions WHERE user_id=?",(self.actor,))
            return inputs
        with patch.object(A,"prepare_admin_verification",side_effect=revoke): self.post("/admin/users/create",self.creation(),self.creds())
        with A.db() as c: self.assertIsNone(c.execute("SELECT id FROM users WHERE username=?",(self.creation()["username"],)).fetchone())

    def test_mfa_off_still_requires_admin_password(self):
        with patch.dict(A.app.config,MFA_ENABLED=False,MFA_ENROLLMENT_ENABLED=False):
            before=self.snapshot(); self.post("/admin/users/create",self.creation()); self.assertEqual(before,self.snapshot())
            self.post("/admin/users/create",self.creation(),{"current_password":self.password})
            with A.db() as c: self.assertIsNotNone(c.execute("SELECT id FROM users WHERE username=?",(self.creation()["username"],)).fetchone())

    def test_csrf_nonadmin_and_consumed_totp_rejected(self):
        before=self.snapshot()
        self.assertEqual(self.client.post("/admin/users/create",data={**self.creation(),**self.creds()}).status_code,400)
        self.post("/admin/users/create",self.creation(),{"current_password":self.password,"method":"totp","code":self.enrollment_code})
        self.assertEqual(before,self.snapshot())
        with A.db() as c: c.execute("UPDATE users SET role='FULL_ACCESS' WHERE id=?",(self.actor,))
        self.assertEqual(self.post("/admin/users/create",self.creation(),self.creds()).status_code,403)

    def test_factor_failures_rate_limited(self):
        for _ in range(5): self.post("/admin/users/create",self.creation(),{**self.creds(),"code":"0000-0000-0000-0000-0000"})
        self.assertEqual(self.post("/admin/users/create",self.creation(),self.creds()).status_code,429)

    def test_fresh_totp_succeeds_once(self):
        with A.db() as c: step=c.execute("SELECT last_used_step FROM user_mfa WHERE user_id=?",(self.actor,)).fetchone()[0]+1
        stamp=step*30+1; code=state["pyotp"].TOTP(self.secret).at(stamp)
        with patch.object(A.mfa.time,"time",return_value=stamp):
            self.post("/admin/users/create",self.creation(),{"current_password":self.password,"method":"totp","code":code})
            with A.db() as c: self.assertIsNotNone(c.execute("SELECT id FROM users WHERE username=?",(self.creation()["username"],)).fetchone())
            before=self.snapshot()
            self.post("/admin/users/create",{**self.creation(),"username":"totpreplay"+str(self.serial)},{"current_password":self.password,"method":"totp","code":code})
            self.assertEqual(before,self.snapshot())

    def test_actor_role_change_rechecked_under_lock(self):
        original=A.prepare_admin_verification
        def demote(url):
            inputs=original(url)
            with A.db() as c: c.execute("UPDATE users SET role='USER' WHERE id=?",(self.actor,))
            return inputs
        with patch.object(A,"prepare_admin_verification",side_effect=demote): self.post("/admin/users/create",self.creation(),self.creds())
        with A.db() as c: self.assertIsNone(c.execute("SELECT id FROM users WHERE username=?",(self.creation()["username"],)).fetchone())

    def test_unreadable_factor_fails_closed(self):
        before=self.snapshot()
        with patch.dict(A.app.extensions,mfa_cipher=state["Fernet"](state["Fernet"].generate_key())):
            result=self.post("/admin/users/create",self.creation(),{"current_password":self.password,"method":"totp","code":"123456"})
        self.assertEqual(result.status_code,503); self.assertEqual(before,self.snapshot())

    def test_forms_and_logs_do_not_expose_secrets(self):
        for path in ("/admin/users/create",self.base):
            html=self.client.get(path).get_data(as_text=True)
            self.assertIn('name="current_password"',html); self.assertIn('name="code"',html)
            for value in (self.password,self.secret,*self.codes): self.assertNotIn(value,html)
        self.post("/admin/users/create",self.creation(),{**self.creds(),"current_password":"Synthetic-Wrong-Secret!"})
        output=(TMP/"app.log").read_text(encoding="utf-8")
        for value in (self.password,self.secret,*self.codes,"Synthetic-Wrong-Secret!"): self.assertNotIn(value,output)

if __name__=="__main__": unittest.main()
