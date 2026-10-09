"""Atomic import history checks in disposable copies with synthetic workbooks."""
import json, runpy, unittest
from pathlib import Path
from contextlib import closing
state=runpy.run_path(str(Path(__file__).resolve().parent/"test_import_safety.py"))
A=state["A"]; TMP=state["TMP"]; base=state["ReplacementTests"]
class ImportHistoryTests(unittest.TestCase):
    serial=0; setUp=base.setUp; workbook=base.workbook; snapshot=base.snapshot
    def client(self):
        client=A.app.test_client()
        with A.app.test_request_context():
            with closing(A.db()) as c:u=c.execute("SELECT * FROM users WHERE id=1").fetchone()
            A.start_session(u); values=dict(A.session)
        with client.session_transaction() as s:s.update(values)
        return client
    def token(self,client):
        import re
        return re.search(r'name="csrf_token" value="([^"]+)"',client.get("/admin/imports").get_data(as_text=True)).group(1)
    def fail_success_history(self):
        with closing(A.db()) as c,c: c.execute("CREATE TRIGGER synthetic_history_failure BEFORE INSERT ON imports WHEN NEW.status='COMPLETED' BEGIN SELECT RAISE(ABORT,'synthetic history failure'); END")
    def history(self):
        with closing(A.db()) as c:return [tuple(r) for r in c.execute("SELECT status,records_processed,records_imported,duplicates_found,errors_found FROM imports WHERE country=?",(self.country,))]
    def standard(self,client):
        return client.post("/admin/imports/upload/confirm",data={"csrf_token":self.token(client),"pending":self.pending.name,"country":self.country},follow_redirects=True)
    def pending_workbook(self):
        self.workbook([("Synthetic Original","Original Contact"),("Synthetic New","New Contact")])
        self.pending=A.IMPORTS/("abcdef012345_"+self.path.name)
        self.pending.write_bytes(self.path.read_bytes())
    def mapped(self,client):
        p={"name":"history_test","title":"Synthetic history","country":self.country,"header_row":1,"columns":[{"header":"Company","field":"company_name","required":True},{"header":"Name","field":"name"}]}
        (TMP/"utils/import_profiles/history_test.json").write_text(json.dumps(p))
        plan=A.profile_import.analyze(self.pending,A.DB,p,mode="update")
        return client.post("/admin/imports/mapped/history_test/confirm",data={"csrf_token":self.token(client),"pending":self.pending.name,"plan":plan["hash"],"mode":"update"})
    def test_standard_history_failure_rolls_back_dataset_and_grants(self):
        self.pending_workbook(); before=self.snapshot(); self.fail_success_history()
        try:
            response=self.standard(self.client()); self.assertEqual(response.status_code,200); self.assertEqual(before,self.snapshot()); self.assertEqual(self.history(),[("FAILED",0,0,0,1)])
            self.assertIn(b"previous active dataset was kept",response.data)
        finally:
            with closing(A.db()) as c,c:c.execute("DROP TRIGGER synthetic_history_failure")
    def test_standard_success_writes_exactly_one_history(self):
        self.pending_workbook(); response=self.standard(self.client())
        self.assertEqual(response.status_code,200); self.assertEqual(self.history(),[("COMPLETED",2,2,0,0)])
        with closing(A.db()) as c:self.assertEqual(c.execute("SELECT id FROM companies WHERE company_name='Synthetic Original' AND country_id=?",(self.cid,)).fetchone()[0],self.co)
    def test_mapped_history_failure_rolls_back_new_and_updated_records(self):
        self.pending_workbook(); before=self.snapshot(); self.fail_success_history()
        try:
            response=self.mapped(self.client()); self.assertEqual(response.status_code,302); self.assertEqual(before,self.snapshot()); self.assertEqual(self.history(),[("FAILED",0,0,0,1)]); self.assertTrue(self.pending.exists())
        finally:
            with closing(A.db()) as c,c:c.execute("DROP TRIGGER synthetic_history_failure")
    def test_mapped_success_writes_exactly_one_history(self):
        self.pending_workbook(); response=self.mapped(self.client())
        self.assertEqual(response.status_code,200); self.assertEqual(self.history(),[("COMPLETED",2,2,0,0)])
        with closing(A.db()) as c:
            self.assertIsNotNone(c.execute("SELECT 1 FROM user_company_access WHERE company_id=?",(self.co,)).fetchone()); self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0],"ok"); self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])
    def test_preview_does_not_call_success_callback(self):
        self.workbook([("Synthetic Original","Original Contact")]); before=self.snapshot()
        def forbidden(*args):raise AssertionError("Preview must not record success")
        A.import_workbook(self.path,A.DB,self.country,replace_country=True,dry_run=True,on_success=forbidden); self.assertEqual(before,self.snapshot()); self.assertEqual(self.history(),[])

del base
if __name__=="__main__": unittest.main()
