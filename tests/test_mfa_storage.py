"""Step 2 checks: synthetic keys/databases only; never imports app.py."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from cryptography.fernet import Fernet
from utils import mfa


class MFAStorageTests(unittest.TestCase):
    def setUp(self):
        self.folder=Path(tempfile.mkdtemp(prefix="mfa-storage-test-"))
        self.database=self.folder/"synthetic.db"
        self.key=Fernet.generate_key().decode("ascii")
        self.env={"MFA_ENCRYPTION_KEY":self.key,"SECRET_KEY":"synthetic-session-key"}
        self.secret="A"*32
        with closing(sqlite3.connect(self.database)) as con:
            con.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT,password_hash TEXT,role TEXT,status TEXT);
                INSERT INTO users VALUES(7,'synthetic-admin','synthetic-hash','ADMIN','ACTIVE');
                CREATE TABLE security_sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),started REAL,seen REAL);
                CREATE TABLE synthetic_records(id INTEGER PRIMARY KEY,value TEXT);
                INSERT INTO synthetic_records VALUES(12,'synthetic record');
                CREATE TABLE synthetic_grants(user_id INTEGER,record_id INTEGER);
                INSERT INTO synthetic_grants VALUES(7,12);
            """)

    def cli(self,*args,env=None):
        values=dict(os.environ)
        for name in ("MFA_ENCRYPTION_KEY","MFA_ENCRYPTION_KEY_FILE","SECRET_KEY"):
            values.pop(name,None)
        values.update(self.env if env is None else env)
        return subprocess.run([sys.executable,"-m","utils.mfa",*map(str,args)],
                              cwd=Path(__file__).resolve().parent.parent,
                              env=values,capture_output=True,text=True)

    def test_encrypt_roundtrip_and_new_cipher_after_restart(self):
        encrypted=mfa.encrypt_secret(mfa.configured_cipher(self.env),7,self.secret)
        self.assertNotIn(self.secret,encrypted)
        self.assertEqual(mfa.decrypt_secret(mfa.configured_cipher(self.env),7,encrypted),self.secret)
        self.assertNotEqual(encrypted,mfa.encrypt_secret(mfa.configured_cipher(self.env),7,self.secret))

    def test_wrong_key_tampering_and_account_swap_rejected(self):
        cipher=mfa.configured_cipher(self.env)
        encrypted=mfa.encrypt_secret(cipher,7,self.secret)
        for other,uid,token in ((Fernet(Fernet.generate_key()),7,encrypted),(cipher,8,encrypted),
                                (cipher,7,encrypted[:30]+("A" if encrypted[30]!="A" else "B")+encrypted[31:])):
            with self.assertRaises(mfa.MFASecretError) as error:
                mfa.decrypt_secret(other,uid,token)
            self.assertNotIn(self.secret,str(error.exception))
            self.assertNotIn(encrypted,str(error.exception))

    def test_missing_invalid_duplicate_and_shared_key_configuration(self):
        for env in ({},{"MFA_ENCRYPTION_KEY":"invalid"},
                    {**self.env,"MFA_ENCRYPTION_KEY_FILE":"unused"},
                    {**self.env,"SECRET_KEY":self.key},
                    {"MFA_ENCRYPTION_KEY_FILE":str(self.folder/"missing-key")}):
            with self.assertRaises(mfa.MFAConfigurationError) as error:
                mfa.configured_cipher(env)
            self.assertNotIn(self.key,str(error.exception))

    def test_key_file_creation_and_no_overwrite(self):
        path=self.folder/"synthetic-key"
        mfa.generate_key_file(path)
        first=path.read_bytes()
        cipher=mfa.configured_cipher({"MFA_ENCRYPTION_KEY_FILE":str(path)})
        self.assertIsInstance(cipher,Fernet)
        with self.assertRaises(FileExistsError): mfa.generate_key_file(path)
        self.assertEqual(path.read_bytes(),first)

    def test_invalid_secret_inputs_rejected(self):
        cipher=mfa.configured_cipher(self.env)
        for uid,secret in ((0,self.secret),(True,self.secret),(7,"short"),(7,"1"*32)):
            with self.assertRaises(mfa.MFASecretError): mfa.encrypt_secret(cipher,uid,secret)
        for value in (None,"x"*2049,"invalid"):
            with self.assertRaises(mfa.MFASecretError): mfa.decrypt_secret(cipher,7,value)

    def test_migration_preserves_records_and_is_repeatable(self):
        with closing(mfa.connect_existing(self.database)) as con:
            original={table:con.execute(f"SELECT * FROM {table}").fetchall()
                      for table in ("users","synthetic_records","synthetic_grants")}
            self.assertTrue(mfa.migrate(con))
            self.assertFalse(mfa.migrate(con))
            for table,rows in original.items(): self.assertEqual(con.execute(f"SELECT * FROM {table}").fetchall(),rows)
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(),[])
            self.assertEqual(con.execute("SELECT COUNT(*) FROM user_mfa").fetchone()[0],0)

    def test_foreign_keys_constraints_and_cascade(self):
        with closing(mfa.connect_existing(self.database)) as con:
            mfa.migrate(con)
            for sql in ("INSERT INTO user_mfa(user_id) VALUES(999)",
                        "INSERT INTO user_mfa(user_id,required) VALUES(7,2)",
                        "INSERT INTO user_mfa(user_id,encrypted_secret) VALUES(7,'ciphertext')"):
                with self.assertRaises(sqlite3.IntegrityError): con.execute(sql)
                con.rollback()
            con.execute("INSERT INTO user_mfa(user_id) VALUES(7)")
            con.execute("INSERT INTO mfa_recovery_codes VALUES(?,?,?,NULL)",(7,"a"*64,"synthetic-time"))
            con.execute("INSERT INTO mfa_challenges VALUES(?,?,?,?,?,?,?,NULL)",("b"*64,7,"login","pw-version",0,1,2))
            con.execute("DELETE FROM users WHERE id=7")
            for table in ("user_mfa","mfa_recovery_codes","mfa_challenges"):
                self.assertEqual(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],0)

    def test_step2_schema_upgrade_preserves_enrollment_and_adds_session_proofs(self):
        with closing(mfa.connect_existing(self.database)) as con:
            for name,definition in mfa.TABLES.items():
                if name!="mfa_session_proofs":
                    definition=definition.replace("recovery_required INTEGER NOT NULL DEFAULT 0 CHECK(recovery_required IN (0,1)),","")
                    con.execute(f"CREATE TABLE {name} ({definition})")
            con.execute("INSERT INTO mfa_schema_versions VALUES(1)")
            con.execute("INSERT INTO user_mfa(user_id,required) VALUES(7,1)"); con.commit()
            self.assertTrue(mfa.migrate(con))
            self.assertEqual(con.execute("SELECT required FROM user_mfa WHERE user_id=7").fetchone()[0],1)
            self.assertEqual(con.execute("SELECT recovery_required FROM user_mfa WHERE user_id=7").fetchone()[0],0)
            self.assertFalse(mfa.migrate(con))
            con.execute("INSERT INTO security_sessions VALUES('synthetic-token',7,1,1)")
            con.execute("INSERT INTO mfa_session_proofs VALUES('synthetic-token',7,0,1)")
            con.execute("DELETE FROM security_sessions WHERE token_hash='synthetic-token'")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM mfa_session_proofs").fetchone()[0],0)

    def test_partial_or_future_schema_refused(self):
        with closing(mfa.connect_existing(self.database)) as con:
            con.execute("CREATE TABLE user_mfa(user_id INTEGER)"); con.commit()
            with self.assertRaises(RuntimeError): mfa.migrate(con)
            self.assertEqual(con.execute("SELECT name FROM sqlite_master WHERE name='mfa_challenges'").fetchall(),[])

    def test_migration_failure_rolls_back_all_tables(self):
        class FailedConnection(sqlite3.Connection):
            def execute(self,sql,*args):
                if sql.startswith("CREATE TABLE IF NOT EXISTS mfa_challenges"):
                    raise sqlite3.OperationalError("synthetic migration failure")
                return super().execute(sql,*args)
        with closing(sqlite3.connect(self.database,factory=FailedConnection)) as con:
            with self.assertRaises(sqlite3.OperationalError): mfa.migrate(con)
            for table in mfa.TABLES:
                self.assertEqual(con.execute("SELECT name FROM sqlite_master WHERE name=?",(table,)).fetchall(),[])

    def test_preview_does_not_modify_database(self):
        before=self.database.read_bytes()
        result=self.cli("migrate","--database",self.database,env={})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(before,self.database.read_bytes())

    def test_missing_database_not_created(self):
        path=self.folder/"missing.db"
        result=self.cli("migrate","--database",path)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(path.exists())

    def test_cli_requires_key_and_backup_then_migrates(self):
        backup=self.folder/"before-mfa.db"
        for args,env in (([],self.env),(["--backup",backup],{})):
            result=self.cli("migrate","--database",self.database,"--apply",*args,env=env)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse(backup.exists())
        result=self.cli("migrate","--database",self.database,"--apply","--backup",backup)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn(self.key,result.stdout+result.stderr)
        with closing(sqlite3.connect(backup)) as con:
            self.assertEqual(con.execute("SELECT * FROM synthetic_records").fetchall(),[(12,"synthetic record")])
            self.assertEqual(con.execute("SELECT name FROM sqlite_master WHERE name='user_mfa'").fetchall(),[])
        first=backup.read_bytes()
        result=self.cli("migrate","--database",self.database,"--apply","--backup",backup)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(backup.read_bytes(),first)

    def test_keygen_cli_does_not_print_key(self):
        target=self.folder/"cli-key"
        result=self.cli("keygen","--output",target)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn(target.read_text().strip(),result.stdout+result.stderr)
        self.assertNotEqual(self.cli("keygen","--output",target).returncode,0)


if __name__=="__main__": unittest.main()
