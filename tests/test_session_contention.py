"""Session contention checks in a disposable app with synthetic SQLite users."""
import runpy, time, unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
state=runpy.run_path(str(Path(__file__).resolve().parent/"test_import_safety.py"))
A=state["A"]
class SessionContentionTests(unittest.TestCase):
    def setUp(self):
        self.client=A.app.test_client()
        with A.app.test_request_context():
            with closing(A.db()) as c: user=c.execute("SELECT * FROM users WHERE id=1").fetchone()
            A.start_session(user); values=dict(A.session)
        with self.client.session_transaction() as s: s.update(values); s["seen"]=int(time.time())-61; self.sid=s["sid"]
        with closing(A.db()) as c,c: c.execute("UPDATE security_sessions SET seen=? WHERE token_hash=?",(time.time()-61,A.security.opaque(self.sid)))
    def test_real_writer_lock_returns_503_then_recovers(self):
        with closing(A.db()) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            response=self.client.get("/search?q=Synthetic")
            self.assertEqual(response.status_code,503); self.assertEqual(response.headers["Retry-After"],"5")
            self.assertEqual(response.headers["Cache-Control"],"no-store"); self.assertNotIn(b"database is locked",response.data)
            with self.client.session_transaction() as s: self.assertEqual(s["sid"],self.sid)
            blocker.rollback()
        self.assertEqual(self.client.get("/search").status_code,200)
    def test_unrelated_database_error_is_not_converted_to_busy(self):
        with closing(A.db()) as c:
            try: c.execute("SELECT * FROM synthetic_nonexistent_table")
            except A.sqlite3.OperationalError as e: error=e
        with patch.object(A,"load_current_user",side_effect=error):
            with self.assertRaises(A.sqlite3.OperationalError): self.client.get("/search")
    def test_locked_extended_code_and_safe_error_render(self):
        error=A.sqlite3.OperationalError("synthetic private error"); error.sqlite_errorcode=A.sqlite3.SQLITE_LOCKED | (1<<8)
        with patch.object(A,"load_current_user",side_effect=error) as loader: response=self.client.get("/admin")
        self.assertEqual(response.status_code,503); self.assertEqual(loader.call_count,1); self.assertNotIn(b"synthetic private error",response.data)
    def test_expired_and_revoked_sessions_remain_denied(self):
        with self.client.session_transaction() as s: s["started"]=int(time.time())-A.SESSION_MAX-1
        self.assertEqual(self.client.get("/admin").status_code,302)
        self.setUp()
        with closing(A.db()) as c,c: c.execute("DELETE FROM security_sessions WHERE token_hash=?",(A.security.opaque(self.sid),))
        self.assertEqual(self.client.get("/admin").status_code,302)

if __name__=="__main__": unittest.main()
