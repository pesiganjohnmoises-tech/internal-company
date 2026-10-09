"""Standard replacement regression checks; disposable code and synthetic SQLite/XLSX only."""
import importlib, os, re, shutil, sqlite3, sys, tempfile, unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from openpyxl import Workbook

SRC=Path(__file__).resolve().parent.parent
TMP=Path(tempfile.mkdtemp(prefix="directory-import-safety-"))
for name in ("app.py","utils","templates","static"):
    source=SRC/name
    if source.is_dir(): shutil.copytree(source,TMP/name,ignore=shutil.ignore_patterns("__pycache__"))
    else: shutil.copy2(source,TMP/name)
os.environ.update(SECRET_KEY="synthetic-import-test-key",ADMIN_PASSWORD="Synthetic-Admin-Password!",
                  DEPLOY_SECRET="",COOKIE_SECURE="0",APP_ENV="development",CLIENT_IP_HEADER="",TRUSTED_PROXY_CIDRS="",
                  MFA_ENABLED="0",MFA_ENROLLMENT_ENABLED="0",MFA_ENCRYPTION_KEY="",MFA_ENCRYPTION_KEY_FILE="")
os.chdir(TMP); sys.path.insert(0,str(TMP))
A=importlib.import_module("app"); importer=importlib.import_module("utils.importer")
A.app.config.update(TESTING=True,WTF_CSRF_ENABLED=True)

class ReplacementTests(unittest.TestCase):
    serial=0
    def setUp(self):
        type(self).serial+=1; self.country="Synthetic Country "+str(self.serial); self.path=TMP/(str(self.serial)+".xlsx")
        with closing(A.db()) as c,c:
            self.cid=c.execute("INSERT INTO countries(name) VALUES(?)",(self.country,)).lastrowid
            self.co=c.execute("INSERT INTO companies(country_id,company_name) VALUES(?,'Synthetic Original')",(self.cid,)).lastrowid
            c.execute("INSERT INTO contacts(company_id,name) VALUES(?,'Original Contact')",(self.co,))
            c.execute("INSERT INTO user_country_access(user_id,country_id,all_companies) VALUES(1,?,0)",(self.cid,))
            c.execute("INSERT INTO user_company_access(user_id,company_id) VALUES(1,?)",(self.co,))
    def workbook(self,rows,headers=("Company","Name")):
        w=Workbook(); s=w.active
        if headers is not None: s.append(headers)
        for row in rows: s.append(row)
        w.save(self.path); w.close()
    def snapshot(self):
        with closing(A.db()) as c:
            return {t:[tuple(r) for r in c.execute("SELECT * FROM "+t)] for t in
                    ("countries","companies","contacts","user_country_access","user_company_access","user_field_access")}
    def test_invalid_and_empty_replacements_never_open_database(self):
        for rows,headers in (([(None,"Invalid")],("Company","Name")),
                             ([("Synthetic Original","Valid"),(None,"Invalid")],("Company","Name")),
                             ([],("Company","Name")), ([(None,None)],("Company","Name")), ([],None)):
            self.workbook(rows,headers); before=self.snapshot(); bytes_before=A.DB.read_bytes()
            for dry in (True,False):
                with patch.object(importer.sqlite3,"connect",side_effect=AssertionError("Validation must precede database access")):
                    with self.assertRaises(ValueError): A.import_workbook(self.path,A.DB,self.country,replace_country=True,dry_run=dry)
                self.assertEqual(before,self.snapshot()); self.assertEqual(bytes_before,A.DB.read_bytes())
    def test_index_upgrade_is_idempotent_and_preserves_data(self):
        before=self.snapshot()
        with closing(A.db()) as c,c: c.execute("DROP INDEX idx_contacts_company")
        A.init_db(); A.init_db()
        self.assertEqual(before,self.snapshot())
        with closing(A.db()) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='idx_contacts_company'").fetchone()[0],1)
            self.assertEqual([r["name"] for r in c.execute("PRAGMA index_info(idx_contacts_company)")],["company_id"])
            plan=" ".join(r["detail"] for r in c.execute("EXPLAIN QUERY PLAN SELECT COUNT(*) FROM contacts WHERE company_id=?",(self.co,)))
            self.assertIn("idx_contacts_company",plan)
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0],"ok"); self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])
    def test_actionable_safe_errors(self):
        self.workbook([(None,"Private workbook text")])
        with self.assertRaises(A.ReplacementValidationError) as err: A.import_workbook(self.path,A.DB,self.country,replace_country=True)
        message=A.import_error_message(err.exception)
        self.assertIn("Row 2: missing company name",message); self.assertIn("preview again",message)
        self.assertNotIn("Private workbook text",message)
        self.workbook([])
        with self.assertRaises(A.ReplacementValidationError) as err: A.import_workbook(self.path,A.DB,self.country,replace_country=True)
        self.assertIn("Delete country",A.import_error_message(err.exception))
    def test_valid_preview_and_apply_preserve_ids_and_grants(self):
        self.workbook([("Synthetic Original","Updated Contact"),("Synthetic New","New Contact"),(None,None)])
        before=self.snapshot(); preview=A.import_workbook(self.path,A.DB,self.country,replace_country=True,dry_run=True)
        self.assertEqual(before,self.snapshot()); self.assertEqual(preview["imported"],2)
        result=A.import_workbook(self.path,A.DB,self.country,replace_country=True)
        self.assertEqual(result["imported"],2)
        with closing(A.db()) as c:
            self.assertEqual(c.execute("SELECT id FROM companies WHERE country_id=? AND company_name='Synthetic Original'",(self.cid,)).fetchone()[0],self.co)
            self.assertIsNotNone(c.execute("SELECT 1 FROM user_company_access WHERE user_id=1 AND company_id=?",(self.co,)).fetchone())
            self.assertEqual(c.execute("SELECT name FROM contacts WHERE company_id=?",(self.co,)).fetchone()[0],"Updated Contact")
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0],"ok"); self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])
    def test_injected_write_failure_rolls_back(self):
        self.workbook([("Synthetic Original","Updated Contact")]); before=self.snapshot()
        with closing(A.db()) as c,c: c.execute("CREATE TRIGGER synthetic_import_failure BEFORE INSERT ON contacts BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        try:
            with self.assertRaises(sqlite3.IntegrityError): A.import_workbook(self.path,A.DB,self.country,replace_country=True)
            self.assertEqual(before,self.snapshot())
        finally:
            with closing(A.db()) as c,c: c.execute("DROP TRIGGER synthetic_import_failure")
    def test_nonreplacement_skip_behavior_unchanged(self):
        self.workbook([(None,"Invalid"),("Synthetic Added","Valid")])
        r=A.import_workbook(self.path,A.DB,self.country)
        self.assertEqual(r["imported"],1); self.assertEqual(len(r["errors"]),1)
    def test_preview_and_confirmation_reject_invalid_workbook(self):
        self.workbook([(None,"Invalid")]); pending=A.IMPORTS/("abcdef012345_"+self.path.name); shutil.copy2(self.path,pending)
        client=A.app.test_client()
        def token(path): return re.search(r'name="csrf_token" value="([^"]+)"',client.get(path).get_data(as_text=True)).group(1)
        client.post("/login",data={"username":"admin","password":"Synthetic-Admin-Password!","csrf_token":token("/login")})
        before=self.snapshot()
        response=client.post("/admin/imports",data={"csrf_token":token("/admin/imports"),"country":self.country,"file":(self.path.open("rb"),"synthetic.xlsx")},content_type="multipart/form-data",follow_redirects=True)
        self.assertIn(b"Row 2: missing company name",response.data); self.assertEqual(before,self.snapshot())
        response=client.post("/admin/imports/upload/confirm",data={"csrf_token":token("/admin/imports"),"pending":pending.name,"country":self.country},follow_redirects=True)
        self.assertIn(b"Row 2: missing company name",response.data); self.assertEqual(before,self.snapshot())

if __name__=="__main__": unittest.main()
