"""Synthetic queue/storage/backup checks. Never imports the working app or company data."""
import concurrent.futures
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from openpyxl import Workbook
from utils import workbook_access as P, workbook_submissions as S
spec=importlib.util.spec_from_file_location('submission_backup',ROOT/'scripts/scheduled_backup.py')
B=importlib.util.module_from_spec(spec); spec.loader.exec_module(B)

class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='workbook-submission-test-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.project=self.root/'project'; self.project.mkdir()
        for folder in ('data','upload_archive'): (self.project/folder).mkdir()
        self.database=self.project/'directory.db'
        with self.con() as con:
            con.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT,role TEXT,status TEXT);
                INSERT INTO users VALUES(1,'submitter','FULL_ACCESS','ACTIVE'),(2,'other','FULL_ACCESS','ACTIVE'),
                    (3,'admin','ADMIN','ACTIVE'),(4,'ordinary','USER','ACTIVE');
                CREATE TABLE countries(id INTEGER PRIMARY KEY,name TEXT,code TEXT);
                INSERT INTO countries VALUES(1,'Synthetic country','SYN'),(2,'Other country','OTH');
                CREATE TABLE imports(id INTEGER PRIMARY KEY);
                CREATE TABLE companies(id INTEGER PRIMARY KEY,name TEXT);
                INSERT INTO companies VALUES(77,'Untouched live company');
                CREATE TABLE contacts(id INTEGER PRIMARY KEY,name TEXT);
                INSERT INTO contacts VALUES(88,'Untouched live contact');
            """)
            con.executescript(P.SCHEMA); con.executescript(S.SCHEMA)
            con.executemany('INSERT INTO user_workbook_upload_access VALUES(?,1)',[(1,),(2,)])
        self.original=self.live_snapshot()

    def con(self):
        con=sqlite3.connect(self.database);con.row_factory=sqlite3.Row;con.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(con.close)
        return con

    def live_snapshot(self):
        with self.con() as con:
            return {table:[tuple(row) for row in con.execute('SELECT * FROM '+table)]
                    for table in ('users','countries','companies','contacts','imports','user_workbook_upload_access')}

    def workbook(self,rows=None,headers=None):
        wb=Workbook(); ws=wb.active;ws.title='Country records'
        ws.append(headers or ['Company Name','Name','Email','Country'])
        for row in rows if rows is not None else [['Synthetic Logistics','Test contact','person@example.test','Synthetic country']]: ws.append(row)
        out=io.BytesIO();wb.save(out);wb.close();out.seek(0);return out

    def submit(self,stream=None,**kwargs):
        values=dict(user_id=1,country_id=1,filename='country.xlsx',complete_country=True,note='Synthetic update')
        values.update(kwargs)
        return S.create_submission(self.project,stream=stream or self.workbook(),**values)

    def test_pending_is_durable_private_and_does_not_import(self):
        row=self.submit()
        self.assertEqual(row['status'],'PENDING_REVIEW');self.assertEqual(len(row['id']),32)
        self.assertEqual(json.loads(row['validation_summary'])['rows'],1)
        self.assertEqual(json.loads(row['validation_summary'])['worksheet'],'Country records')
        self.assertEqual(S.list_submissions(self.project,1)[0]['id'],row['id'])
        with S.verified_workbook(self.project,1,row['id']) as stream:
            self.assertEqual(S._digest(stream),(row['sha256'],row['size_bytes']))
        with self.con() as con:
            self.assertEqual([r[0] for r in con.execute('SELECT event FROM workbook_submission_events ORDER BY id')],['SUBMITTED','VALIDATED'])
        self.assertEqual(self.live_snapshot(),self.original)
        self.assertFalse(list((self.project/'workbook_submissions').glob('*.partial')))

    def test_permission_and_country_denied_before_record_creation(self):
        for user,country in ((4,1),(3,1),(1,2),(999,1)):
            with self.assertRaises(PermissionError):self.submit(user_id=user,country_id=country)
        self.assertEqual(S.list_submissions(self.project,3),[])
        with self.assertRaises(PermissionError):S.list_submissions(self.project,4)
        self.assertEqual(self.live_snapshot(),self.original)

    def test_only_owner_or_active_admin_can_read_private_files(self):
        row=self.submit()
        self.assertEqual(S.list_submissions(self.project,2),[])
        with self.assertRaises(PermissionError):
            with S.verified_workbook(self.project,2,row['id']):pass
        with S.verified_workbook(self.project,3,row['id']) as stream:self.assertTrue(stream.read(2))
        with self.con() as con:con.execute("UPDATE users SET status='DISABLED' WHERE id=1")
        with self.assertRaises(PermissionError):S.list_submissions(self.project,1)
        with self.assertRaises(ValueError):
            with S.verified_workbook(self.project,3,'../directory.db'):pass

    def test_validation_rejects_invalid_empty_missing_company_duplicate_and_country_mismatch(self):
        cases=[io.BytesIO(b'not an XLSX file'),self.workbook(rows=[]),self.workbook(headers=['Email'],rows=[['x@example.test']]),
               self.workbook(rows=[['','Name','a@example.test','Synthetic country']]),
               self.workbook(rows=[['Corp','Name','a@example.test','Other country']]),
               self.workbook(rows=[['Corp','Same','a@example.test','SYN']]*2),
               self.workbook(headers=['Company Name','company_name'],rows=[['Corp','Corp']])]
        for stream in cases:
            row=self.submit(stream=stream)
            self.assertEqual(row['status'],'VALIDATION_FAILED',row['feedback'])
            self.assertTrue(row['feedback']);self.assertNotIn('person@example.test',row['feedback'])
        self.assertEqual(self.live_snapshot(),self.original)

    def test_complete_country_confirmation_filenames_and_notes(self):
        for name in ('../country.xlsx',r'C:\\country.xlsx','country.xls','country.xlsx\n'):
            with self.assertRaises(ValueError):self.submit(filename=name)
        with self.assertRaises(ValueError):self.submit(complete_country=False)
        with self.assertRaises(ValueError):self.submit(note='x'*2001)
        self.assertEqual(S.list_submissions(self.project,3),[])

    def test_size_expansion_and_row_limits_are_bounded(self):
        with patch.object(S,'MAX_BYTES',20):row=self.submit()
        self.assertEqual(row['status'],'VALIDATION_FAILED');self.assertIsNone(row['sha256'])
        with patch.object(S,'MAX_UNPACKED',20):row=self.submit()
        self.assertEqual(row['status'],'VALIDATION_FAILED')
        with patch.object(S,'MAX_ROWS',1):row=self.submit(stream=self.workbook(rows=[['Corp','A'],['Corp','B']]))
        self.assertEqual(row['status'],'VALIDATION_FAILED')

    def test_duplicate_uploads_are_separate_immutable_records(self):
        payload=self.workbook().getvalue()
        first=self.submit(stream=io.BytesIO(payload));second=self.submit(stream=io.BytesIO(payload))
        self.assertNotEqual(first['id'],second['id']);self.assertEqual(second['duplicate_of'],first['id'])
        for row in (first,second):self.assertTrue((self.project/'workbook_submissions'/(row['id']+'.xlsx')).is_file())
        self.assertEqual(self.live_snapshot(),self.original)

    def test_tampering_blocks_read_and_backup(self):
        row=self.submit();path=self.project/'workbook_submissions'/(row['id']+'.xlsx')
        path.write_bytes(b'synthetic tampering')
        with self.assertRaises(ValueError):
            with S.verified_workbook(self.project,1,row['id']):pass
        destination=self.root/'backups';destination.mkdir()
        with self.assertRaises(ValueError):B.create_backup(self.project,destination,datetime.now(timezone.utc))
        self.assertFalse(list(destination.glob('recovery-*.zip')))

    def test_storage_failure_keeps_a_failed_record_without_touching_previous_upload(self):
        first=self.submit()
        with patch.object(S.os,'link',side_effect=OSError('Synthetic storage failure')):failed=self.submit()
        self.assertEqual(failed['status'],'SUBMISSION_FAILED')
        with S.verified_workbook(self.project,1,first['id']) as stream:self.assertTrue(stream.read(2))
        self.assertEqual(self.live_snapshot(),self.original)

    def test_revoked_permission_during_validation_never_becomes_pending(self):
        original=S._validate
        def revoke(*args):
            with self.con() as con:con.execute('DELETE FROM user_workbook_upload_access WHERE user_id=1')
            return original(*args)
        with patch.object(S,'_validate',side_effect=revoke):row=self.submit()
        self.assertEqual(row['status'],'SUBMISSION_FAILED')
        self.assertTrue(row['sha256'])
        with self.assertRaises(PermissionError):S.list_submissions(self.project,1)

    def test_schema_repeatability_and_history_survives_user_deletion(self):
        row=self.submit()
        with self.con() as con:
            con.executescript(S.SCHEMA);con.executescript(S.SCHEMA)
            con.execute('DELETE FROM users WHERE id=1')
            stored=con.execute('SELECT * FROM workbook_submissions WHERE id=?',(row['id'],)).fetchone()
            self.assertIsNone(stored['uploader_id']);self.assertEqual(stored['uploader_username'],'submitter')
            self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(S.list_submissions(self.project,3)[0]['id'],row['id'])

    def test_deleted_uploader_during_validation_keeps_failure_history(self):
        original=S._validate
        def delete_uploader(*args):
            with self.con() as con:con.execute('DELETE FROM users WHERE id=1')
            return original(*args)
        with patch.object(S,'_validate',side_effect=delete_uploader):row=self.submit()
        self.assertEqual(row['status'],'SUBMISSION_FAILED');self.assertIsNone(row['uploader_id'])
        with self.con() as con:
            event=con.execute('SELECT actor_id,event FROM workbook_submission_events ORDER BY id DESC LIMIT 1').fetchone()
            self.assertIsNone(event['actor_id']);self.assertEqual(event['event'],'SUBMISSION_FAILED')

    def test_backup_restore_includes_private_workbook_and_queue(self):
        row=self.submit();directory=self.project/'workbook_submissions'
        (directory/'.interrupted.partial').write_bytes(b'incomplete')
        destination=self.root/'backups';destination.mkdir()
        archive=B.create_backup(self.project,destination,datetime.now(timezone.utc))
        manifest=B.verify(archive)
        member='workbook_submissions/'+row['id']+'.xlsx'
        self.assertIn(member,manifest['files']);self.assertNotIn('workbook_submissions/.interrupted.partial',manifest['files'])
        restored=self.root/'restored';B.restore(archive,restored)
        with S.verified_workbook(restored,1,row['id']) as stream:self.assertTrue(stream.read(2))
        self.assertEqual(S.list_submissions(restored,1)[0]['status'],'PENDING_REVIEW')
        (directory/(row['id']+'.xlsx')).unlink()
        with self.assertRaises(ValueError):B.create_backup(self.project,destination,datetime.now(timezone.utc))

    def test_concurrent_submissions_do_not_overwrite(self):
        payload=self.workbook().getvalue()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            rows=list(pool.map(lambda _:self.submit(stream=io.BytesIO(payload)),range(2)))
        self.assertEqual(len({row['id'] for row in rows}),2)
        self.assertTrue(all(row['status']=='PENDING_REVIEW' for row in rows))
        self.assertEqual(len(S.list_submissions(self.project,1)),2)

if __name__=='__main__':unittest.main()
