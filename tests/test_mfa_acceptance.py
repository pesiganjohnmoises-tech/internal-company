"""Step 7 directory permission acceptance using a synthetic, disposable app."""
import json
from pathlib import Path
import runpy
import unittest

state=runpy.run_path(str(Path(__file__).resolve().parent/"test_mfa_login.py"))
A=state["A"]; csrf=state["csrf"]; base=state["MFALoginTests"]


class AcceptanceTests(unittest.TestCase):
    serial=0
    setUp=base.setUp
    login=base.login
    post=base.post
    start=base.start
    confirm=base.confirm
    enroll=base.enroll
    verify=base.verify

    def directory(self):
        with A.db() as con:
            countries=[]
            for suffix in ("Granted","Denied"):
                countries.append(con.execute("INSERT INTO countries(name) VALUES(?)",(suffix+str(self.serial),)).lastrowid)
            self.records=[]
            for country,name in ((countries[0],"Allowed Agent"),(countries[0],"Hidden Company"),(countries[1],"Hidden Country")):
                self.records.append(con.execute("INSERT INTO companies(country_id,company_name,source_data) VALUES(?,?,?)",
                    (country,name,json.dumps({"Company":name,"KG Sales":"Synthetic Restricted Sales"}))).lastrowid)
            con.execute("INSERT INTO user_country_access(user_id,country_id,all_companies) VALUES(?,?,0)",(self.uid,countries[0]))
            con.execute("INSERT INTO user_company_access VALUES(?,?)",(self.uid,self.records[0]))
        return countries

    def assert_restricted(self,client):
        html=client.get("/search?q=Agent").get_data(as_text=True)
        self.assertIn("Allowed",html)
        for cid in self.records[1:]: self.assertEqual(client.get(f"/company/{cid}").status_code,404)
        detail=client.get(f"/company/{self.records[0]}")
        self.assertEqual(detail.status_code,200)
        self.assertNotIn("Synthetic Restricted Sales",detail.get_data(as_text=True))
        self.assertEqual(client.get("/admin").status_code,403)
        self.assertEqual(client.get("/admin/export/companies.csv").status_code,403)
        self.assertEqual(client.get("/account/password").status_code,403)

    def test_optional_enrollment_and_mfa_login_preserve_scoped_permissions(self):
        self.directory(); self.assert_restricted(self.client)
        self.enroll(); self.assert_restricted(self.client)
        client=self.login()
        for path in ("/search?q=Agent",f"/company/{self.records[0]}","/admin/export/companies.csv"):
            response=client.get(path)
            self.assertEqual(response.location,"/login/mfa")
            self.assertNotIn("Allowed Agent",response.get_data(as_text=True))
        self.assertEqual(self.verify(client,self.codes[0],"recovery").status_code,302)
        self.assert_restricted(client)

    def test_required_enrollment_preserves_scoped_permissions(self):
        self.directory()
        with A.db() as con: con.execute("INSERT INTO user_mfa(user_id,required) VALUES(?,1)",(self.uid,))
        pending=self.login()
        self.assertEqual(pending.get(f"/company/{self.records[0]}").location,"/login/mfa")
        import re
        secret=re.search(r'<code class="mfa-key">([A-Z2-7]{32})</code>',pending.get("/account/mfa").get_data(as_text=True)).group(1)
        result=pending.post("/account/mfa",data={"action":"confirm","code":state["pyotp"].TOTP(secret).now(),"csrf_token":csrf(pending)})
        self.assertEqual(result.status_code,200); self.assert_restricted(pending)

    def test_full_access_mfa_does_not_grant_administration(self):
        self.directory()
        with A.db() as con: con.execute("UPDATE users SET role='FULL_ACCESS' WHERE id=?",(self.uid,))
        self.client=self.login(); self.enroll()
        for cid in self.records:
            response=self.client.get(f"/company/{cid}")
            self.assertEqual(response.status_code,200)
            self.assertIn("Synthetic Restricted Sales",response.get_data(as_text=True))
        for path in ("/admin","/admin/users","/admin/export/companies.csv","/account/password"):
            self.assertEqual(self.client.get(path).status_code,403)

    def test_country_wide_and_field_grants_still_apply_after_mfa(self):
        countries=self.directory(); self.enroll()
        with A.db() as con:
            con.execute("UPDATE user_country_access SET all_companies=1 WHERE user_id=? AND country_id=?",(self.uid,countries[0]))
            sales_key="x_kgsales"
            con.execute("INSERT INTO user_field_access VALUES(?,?)",(self.uid,sales_key))
        for cid in self.records[:2]:
            result=self.client.get(f"/company/{cid}")
            self.assertEqual(result.status_code,200)
            self.assertIn("Synthetic Restricted Sales",result.get_data(as_text=True))
        self.assertEqual(self.client.get(f"/company/{self.records[2]}").status_code,404)


del base
if __name__=="__main__": unittest.main()
