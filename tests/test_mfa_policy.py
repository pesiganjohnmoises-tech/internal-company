"""Admin MFA policy checks using disposable app copies and synthetic records."""
from pathlib import Path
from html.parser import HTMLParser
import re
import runpy
import unittest
from unittest.mock import patch
from werkzeug.datastructures import MultiDict

state=runpy.run_path(str(Path(__file__).resolve().parent/"test_mfa_enrollment.py"))
A=state["A"]; csrf=state["csrf"]; TMP=state["TMP"]


class PolicyTests(unittest.TestCase):
    serial=0
    setUp=state["EnrollmentTests"].setUp
    login=state["EnrollmentTests"].login
    post=state["EnrollmentTests"].post
    start=state["EnrollmentTests"].start
    confirm=state["EnrollmentTests"].confirm

    def admin(self,pending=False):
        name="policyadmin"+str(self.serial)
        with A.db() as con:
            con.execute("INSERT INTO users(username,password_hash,role,status) VALUES(?,?,?,?)",(name,A.generate_password_hash(self.password),"ADMIN","ACTIVE"))
            self.admin_uid=con.execute("SELECT id FROM users WHERE username=?",(name,)).fetchone()[0]
        client=A.app.test_client()
        client.post("/login",data={"username":name,"password":self.password,"csrf_token":csrf(client,"/login")})
        if not pending:
            html=client.get("/account/mfa").get_data(as_text=True)
            secret=re.search(r'<code class="mfa-key">([A-Z2-7]{32})</code>',html).group(1)
            result=client.post("/account/mfa",data={"action":"confirm","code":state["pyotp"].TOTP(secret).now(),"csrf_token":csrf(client)})
            self.assertEqual(result.status_code,200)
        return client

    def policy(self,admin,value,uid=None):
        uid=self.uid if uid is None else uid
        return admin.post(f"/admin/users/{uid}/mfa-policy",data={"required":str(value),"csrf_token":csrf(admin,f"/admin/users/{uid}")})

    def snapshot(self):
        with A.db() as con:
            return {table:con.execute(f"SELECT * FROM {table} WHERE "+("id=?" if table=="users" else "user_id=?"),(self.uid,)).fetchall()
                    for table in ("users","user_country_access","user_company_access","user_field_access","user_mfa","mfa_recovery_codes","mfa_challenges","security_sessions","mfa_session_proofs")}

    def test_enable_revokes_sessions_and_requires_enrollment(self):
        admin=self.admin(); other=self.login()
        result=self.policy(admin,1)
        self.assertEqual(result.status_code,302)
        self.assertEqual(self.client.get("/search").location,"/login")
        self.assertEqual(other.get("/search").location,"/login")
        pending=self.login()
        html=pending.get("/account/mfa").get_data(as_text=True)
        self.assertIn('class="mfa-key"',html)
        self.assertEqual(pending.get("/search").location,"/login/mfa")
        with A.db() as con:
            row=con.execute("SELECT required,security_version,encrypted_secret FROM user_mfa WHERE user_id=?",(self.uid,)).fetchone()
            self.assertEqual(tuple(row),(1,1,None))
        self.assertIn("Enrollment is pending",admin.get(f"/admin/users/{self.uid}").get_data(as_text=True))

    def test_remove_requirement_keeps_factor_recovery_and_replay_state(self):
        secret=self.start(); response=self.confirm(secret)
        codes=re.findall(r"<code>((?:[A-F0-9]{4}-){4}[A-F0-9]{4})</code>",response.get_data(as_text=True))
        admin=self.admin(); self.policy(admin,1)
        pending=self.login()
        before=self.snapshot()
        self.assertEqual(self.policy(admin,0).status_code,302)
        after=self.snapshot()
        self.assertEqual(after["mfa_recovery_codes"],before["mfa_recovery_codes"])
        self.assertEqual(after["user_mfa"][0][2:5],before["user_mfa"][0][2:5])
        self.assertEqual(after["mfa_challenges"],[])
        self.assertEqual(pending.get("/login/mfa").location,"/login")
        next_client=self.login()
        self.assertIn("Authenticator code",next_client.get("/login/mfa").get_data(as_text=True))
        result=next_client.post("/login/mfa",data={"method":"recovery","code":codes[0],"csrf_token":csrf(next_client,"/login/mfa")})
        self.assertEqual(result.status_code,302)
        self.assertEqual(next_client.get("/search").status_code,200)

    def test_optional_unenrolled_returns_to_password_only(self):
        admin=self.admin(); self.policy(admin,1)
        pending=self.login()
        self.policy(admin,0)
        self.assertEqual(pending.get("/account/mfa").location,"/login")
        self.assertEqual(self.login().get("/search").status_code,200)

    def test_noop_preserves_sessions_and_pending_setups(self):
        admin=self.admin(); self.start(); before=self.snapshot()
        self.assertEqual(self.policy(admin,0).status_code,302)
        after=self.snapshot()
        for table in before: self.assertEqual(after[table],before[table],table)
        self.assertEqual(self.client.get("/account/mfa/qr").status_code,200)

    def test_admin_requirement_cannot_be_disabled_or_role_spoofed(self):
        admin=self.admin()
        for uid in (self.admin_uid,1):
            result=admin.post(f"/admin/users/{uid}/mfa-policy",data={"required":"0","role":"USER","csrf_token":csrf(admin,f"/admin/users/{uid}")})
            self.assertEqual(result.status_code,400)
        html=admin.get(f"/admin/users/{self.admin_uid}").get_data(as_text=True)
        self.assertIn("mandatory for administrators",html)
        self.assertNotIn('name="required"',html)
        self.assertEqual(self.policy(admin,1,self.admin_uid).status_code,302)
        self.assertEqual(admin.get("/admin").status_code,200)

    def test_nonadmins_and_pending_admin_cannot_change_policy(self):
        before=self.snapshot()
        self.assertEqual(self.client.post(f"/admin/users/{self.uid}/mfa-policy",data={"required":"1","csrf_token":csrf(self.client)}).status_code,403)
        with A.db() as con: con.execute("UPDATE users SET role='FULL_ACCESS' WHERE id=?",(self.uid,))
        full=self.login()
        self.assertEqual(full.post(f"/admin/users/{self.uid}/mfa-policy",data={"required":"1","csrf_token":csrf(full)}).status_code,403)
        pending=self.admin(pending=True)
        self.assertEqual(pending.post(f"/admin/users/{self.uid}/mfa-policy",data={"required":"1","csrf_token":csrf(pending)}).location,"/login/mfa")
        with A.db() as con: self.assertIsNone(con.execute("SELECT required FROM user_mfa WHERE user_id=?",(self.uid,)).fetchone())

    def test_csrf_invalid_duplicate_and_missing_values_are_rejected(self):
        admin=self.admin()
        before=self.snapshot()
        for value in (None,"invalid"):
            data={"required":"1"}
            if value: data["csrf_token"]=value
            self.assertEqual(admin.post(f"/admin/users/{self.uid}/mfa-policy",data=data).status_code,400)
        token=csrf(admin,f"/admin/users/{self.uid}")
        with patch.dict(A.app.config,WTF_CSRF_TIME_LIMIT=-1):
            self.assertEqual(admin.post(f"/admin/users/{self.uid}/mfa-policy",data={"required":"1","csrf_token":token}).status_code,400)
        for values in ([],["yes"],[""],["2"],["0","1"]):
            data=MultiDict([("csrf_token",csrf(admin,f"/admin/users/{self.uid}")),*[("required",v) for v in values]])
            self.assertEqual(admin.post(f"/admin/users/{self.uid}/mfa-policy",data=data).status_code,400)
        self.assertEqual(before,self.snapshot())

    def test_policy_does_not_change_password_role_status_or_grants(self):
        admin=self.admin()
        with A.db() as con:
            con.execute("INSERT INTO countries(name) VALUES(?)",("Synthetic Country "+str(self.serial),))
            country=con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.execute("INSERT INTO companies(country_id,company_name) VALUES(?,?)",(country,"Synthetic Company"))
            company=con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.execute("INSERT INTO user_country_access(user_id,country_id,all_companies) VALUES(?,?,0)",(self.uid,country))
            con.execute("INSERT INTO user_company_access VALUES(?,?)",(self.uid,company))
            con.execute("INSERT INTO user_field_access VALUES(?,?)",(self.uid,"x_kgsales"))
        before=self.snapshot(); token=csrf(admin,f"/admin/users/{self.uid}")
        result=admin.post(f"/admin/users/{self.uid}/mfa-policy",data={"required":"1","role":"ADMIN","status":"DISABLED","password":"Synthetic-Malicious-Password!","csrf_token":token})
        self.assertEqual(result.status_code,302)
        after=self.snapshot()
        for table in ("users","user_country_access","user_company_access","user_field_access"):
            self.assertEqual(after[table],before[table],table)

    def test_status_display_full_access_policy_and_flag_off(self):
        admin=self.admin()
        with A.db() as con: con.execute("UPDATE users SET role='FULL_ACCESS' WHERE id=?",(self.uid,))
        self.policy(admin,1)
        html=admin.get(f"/admin/users/{self.uid}").get_data(as_text=True)
        self.assertIn("MFA is required",html); self.assertIn("Enrollment is pending",html)
        self.assertIn("Enrollment pending",admin.get("/admin/users").get_data(as_text=True))
        with patch.dict(A.app.config,MFA_ENABLED=False,MFA_ENROLLMENT_ENABLED=False):
            self.assertNotIn("Sign-in security",admin.get(f"/admin/users/{self.uid}").get_data(as_text=True))
            self.assertNotIn("<th>MFA</th>",admin.get("/admin/users").get_data(as_text=True))
            self.assertEqual(self.policy(admin,0).status_code,404)

    def test_logs_policy_changes_without_factors(self):
        secret=self.start(); result=self.confirm(secret)
        codes=re.findall(r"<code>((?:[A-F0-9]{4}-){4}[A-F0-9]{4})</code>",result.get_data(as_text=True))
        admin=self.admin(); self.policy(admin,1)
        text=(TMP/"app.log").read_text(encoding="utf-8")
        self.assertIn(f"MFA policy changed actor_id={self.admin_uid} target_id={self.uid} required=1",text)
        for value in (secret,*codes): self.assertNotIn(value,text)

    def test_separate_form_and_missing_target(self):
        admin=self.admin()
        class Forms(HTMLParser):
            depth=0
            maximum=0
            actions=[]
            def handle_starttag(self,tag,attrs):
                if tag=="form":
                    self.depth+=1; self.maximum=max(self.maximum,self.depth)
                    self.actions.append(dict(attrs).get("action"))
            def handle_endtag(self,tag):
                if tag=="form": self.depth-=1
        forms=Forms(); forms.feed(admin.get(f"/admin/users/{self.uid}").get_data(as_text=True))
        self.assertEqual(forms.maximum,1)
        self.assertIn(f"/admin/users/{self.uid}/mfa-policy",forms.actions)
        token=csrf(admin,f"/admin/users/{self.uid}")
        result=admin.post("/admin/users/999999/mfa-policy",data={"required":"1","csrf_token":token})
        self.assertEqual(result.status_code,404)

    def test_storage_failure_rolls_back_policy_and_revocations(self):
        admin=self.admin(); self.start(); before=self.snapshot()
        token=csrf(admin,f"/admin/users/{self.uid}")
        revoke=A.revoke_user_mfa
        def fail(con,uid):
            revoke(con,uid)
            raise A.sqlite3.OperationalError("synthetic private error detail")
        with patch.object(A,"revoke_user_mfa",side_effect=fail):
            result=admin.post(f"/admin/users/{self.uid}/mfa-policy",data={"required":"1","csrf_token":token})
        self.assertEqual(result.status_code,503)
        self.assertNotIn("synthetic private error detail",result.get_data(as_text=True))
        self.assertEqual(before,self.snapshot())


if __name__=="__main__": unittest.main()
