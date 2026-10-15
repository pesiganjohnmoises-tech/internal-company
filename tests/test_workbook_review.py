"""Admin review and rejection on synthetic workbooks/disposable application copies."""
from contextlib import closing
import io, re, runpy, unittest
from pathlib import Path
from unittest.mock import patch
from openpyxl import Workbook

state=runpy.run_path(str(Path(__file__).resolve().parent/'test_admin_verification.py'))
A=state['A']; csrf=state['csrf']

class WorkbookReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture=state['AdminVerificationTests'](); self.fixture.setUp()
        self.admin=self.fixture.actor; self.uid=self.fixture.target; self.client=self.fixture.client
        self.country='Review country '+str(self.uid)
        with closing(A.db()) as c,c:
            c.execute("UPDATE users SET role='FULL_ACCESS' WHERE id=?",(self.uid,))
            self.cid=c.execute('INSERT INTO countries(name) VALUES(?)',(self.country,)).lastrowid
            c.execute('INSERT INTO user_workbook_upload_access VALUES(?,?)',(self.uid,self.cid))
        self.old=Path(A.ROOT)/('baseline-'+str(self.uid)+'.xlsx')
        self.old.write_bytes(self.workbook([['Keep','Alice','old@example.test','Original address'],['Remove','Bob','bob@example.test','Other address']]))
        A.import_workbook(self.old,A.DB,self.country,replace_country=True)
        with closing(A.db()) as c,c:
            self.keep=c.execute("SELECT id FROM companies WHERE country_id=? AND company_name='Keep'",(self.cid,)).fetchone()[0]
            self.remove=c.execute("SELECT id FROM companies WHERE country_id=? AND company_name='Remove'",(self.cid,)).fetchone()[0]
            c.execute('INSERT INTO user_company_access VALUES(?,?)',(self.uid,self.remove))
        self.row=self.submit([['Keep','Alice','new@example.test','Long incoming address '*20],['Add','Carol','carol@example.test','New address']])
        self.path='/admin/workbooks/'+self.row['id']

    def workbook(self,rows):
        book=Workbook(); book.active.append(['Company','Name','Email','Address'])
        for row in rows: book.active.append(row)
        stream=io.BytesIO(); book.save(stream); book.close(); return stream.getvalue()

    def submit(self,rows):
        return A.workbook_submissions.create_submission(A.ROOT,self.uid,self.cid,'review.xlsx',io.BytesIO(self.workbook(rows)),
            note='<script>synthetic note</script>',complete_country=True)

    def snapshot(self):
        with closing(A.db()) as c:
            return {t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in
                    ('countries','companies','contacts','imports','user_company_access','user_country_access')}

    def post(self,action,**fields):
        return self.client.post(self.path,data={'csrf_token':csrf(self.client,self.path),'action':action,'confirmed':'1',**fields})

    def test_actual_importer_preview_and_read_only_live_data(self):
        before=self.snapshot()
        result=A.workbook_review.preview(A.ROOT,self.admin,self.row['id'])
        counts=result['counts']
        self.assertEqual({k:counts[k] for k in ('added_companies','removed_companies','updated_contacts','added_contacts','removed_contacts','affected_grants')},
            dict(added_companies=1,removed_companies=1,updated_contacts=1,added_contacts=1,removed_contacts=1,affected_grants=1))
        self.assertTrue(any(f['field']=='email' and f['before']=='old@example.test' and f['after']=='new@example.test'
            for e in result['entries'] for f in e['fields']))
        self.assertTrue(any(e['kind']=='Access impact' for e in result['entries']))
        self.assertEqual(before,self.snapshot())
        response=self.client.get(self.path); self.assertEqual(response.status_code,200)
        self.assertIn('&lt;script&gt;synthetic note&lt;/script&gt;',response.get_data(as_text=True))
        self.assertEqual(self.client.get('/admin/workbooks').status_code,200)
        self.assertIn('Workbook reviews',self.client.get('/admin').get_data(as_text=True))
        self.assertEqual(self.client.get(self.path+'?kind=Updated+contact&q=old%40example.test').status_code,200)
        self.assertEqual(self.client.get(self.path+'/download').status_code,200)
        self.assertEqual(before,self.snapshot())

    def test_unchanged_and_ambiguous_contacts_are_not_id_churn(self):
        row=self.submit([['Keep','Alice','old@example.test','Original address'],['Remove','Bob','bob@example.test','Other address']])
        result=A.workbook_review.preview(A.ROOT,self.admin,row['id'])
        self.assertEqual(result['counts']['unchanged_contacts'],2)
        self.assertEqual(result['counts']['updated_contacts'],0)
        row=self.submit([['Keep','Renamed','other@example.test','Original address']])
        result=A.workbook_review.preview(A.ROOT,self.admin,row['id'])
        self.assertEqual(result['counts']['ambiguous_contacts'],2)
        self.assertTrue(any(e['kind']=='Ambiguous contacts' for e in result['entries']))

    def test_record_review_stale_baseline_and_no_import(self):
        before=self.snapshot(); result=A.workbook_review.preview(A.ROOT,self.admin,self.row['id'])
        self.assertEqual(self.post('review',baseline=result['baseline']).status_code,302)
        saved=A.workbook_submissions.get_submission(A.ROOT,self.admin,self.row['id'])
        self.assertEqual(saved['review_baseline'],result['baseline']); self.assertEqual(saved['status'],'PENDING_REVIEW')
        self.assertEqual(before,self.snapshot())
        with closing(A.db()) as c,c: c.execute("UPDATE contacts SET phone='Changed' WHERE company_id=?",(self.keep,))
        self.assertEqual(self.post('review',baseline=result['baseline']).status_code,409)
        self.assertIn('saved review is stale',self.client.get(self.path).get_data(as_text=True))
        self.assertEqual(self.post('approve').status_code,400)

    def test_rejection_feedback_csrf_and_no_live_changes(self):
        before=self.snapshot()
        for data in ({'action':'reject','reason':'No token'},{'action':'reject','reason':'Bad token','csrf_token':'bad'}):
            self.assertEqual(self.client.post(self.path,data=data).status_code,400)
        self.assertEqual(self.post('reject',reason=' ').status_code,400)
        self.assertEqual(self.post('reject',reason='x'*2001).status_code,400)
        self.assertEqual(self.post('reject',reason='Please include all branches.').status_code,302)
        self.assertEqual(self.post('reject',reason='Replay').status_code,409)
        saved=A.workbook_submissions.get_submission(A.ROOT,self.admin,self.row['id'])
        self.assertEqual(saved['status'],'REJECTED'); self.assertEqual(saved['rejection_reason'],'Please include all branches.')
        self.assertEqual(before,self.snapshot())
        user=A.app.test_client()
        with closing(A.db()) as c: name=c.execute('SELECT username FROM users WHERE id=?',(self.uid,)).fetchone()[0]
        user.post('/login',data={'username':name,'password':'Synthetic-Target-Password!','csrf_token':csrf(user,'/login')})
        self.assertIn('Please include all branches.',user.get('/workbooks/'+self.row['id']).get_data(as_text=True))
        for path in ('/admin/workbooks',self.path,self.path+'/download'):
            self.assertEqual(user.get(path).status_code,403)
        self.assertEqual(user.post(self.path,data={'csrf_token':csrf(user,'/search'),'action':'reject','reason':'forged'}).status_code,403)

    def test_integrity_missing_country_nonadmin_and_failure_rollback(self):
        for role in ('USER','FULL_ACCESS'):
            with closing(A.db()) as c,c: c.execute('UPDATE users SET role=? WHERE id=?',(role,self.admin))
            with self.assertRaises(PermissionError): A.workbook_review.preview(A.ROOT,self.admin,self.row['id'])
            with self.assertRaises(PermissionError): A.workbook_review.reject(A.ROOT,self.admin,self.row['id'],'Forged')
        with closing(A.db()) as c,c: c.execute("UPDATE users SET role='ADMIN' WHERE id=?",(self.admin,))
        before=self.snapshot()
        with patch.object(A.workbook_submissions,'_event',side_effect=sqlite_error()):
            with self.assertRaises(A.sqlite3.OperationalError): A.workbook_review.reject(A.ROOT,self.admin,self.row['id'],'Retry')
        self.assertEqual(A.workbook_submissions.get_submission(A.ROOT,self.admin,self.row['id'])['status'],'PENDING_REVIEW')
        (A.ROOT/'workbook_submissions'/(self.row['id']+'.xlsx')).write_bytes(b'tampered')
        with self.assertRaises(ValueError): A.workbook_review.preview(A.ROOT,self.admin,self.row['id'])
        self.assertEqual(before,self.snapshot())

    def test_country_changes_and_authentication_are_rechecked(self):
        for path in ('/admin/workbooks',self.path,self.path+'/download'):
            self.assertEqual(A.app.test_client().get(path).status_code,302)
        with closing(A.db()) as c,c:
            c.execute("UPDATE countries SET name='Renamed review country '||id WHERE id=?",(self.cid,))
        with self.assertRaises(A.workbook_review.ReviewConflict): A.workbook_review.preview(A.ROOT,self.admin,self.row['id'])
        self.assertEqual(self.client.get(self.path).status_code,409)
        with closing(A.db()) as c,c:
            c.execute('DELETE FROM mfa_session_proofs WHERE user_id=?',(self.admin,))
        self.assertEqual(self.client.get(self.path).status_code,302)

    def test_search_pagination_and_ambiguous_rows_are_bounded(self):
        row=self.submit([['Keep','New '+str(i),'new'+str(i)+'@example.test','Address'] for i in range(30)])
        path='/admin/workbooks/'+row['id']
        html=self.client.get(path+'?kind=Ambiguous+contacts').get_data(as_text=True)
        self.assertEqual(html.count('<article class="review-entry">'),20)
        self.assertIn('>Next</a>',html)
        html=self.client.get(path+'?kind=Ambiguous+contacts&page=2').get_data(as_text=True)
        self.assertLessEqual(html.count('<article class="review-entry">'),20)
        self.assertIn('>Previous</a>',html)
        self.assertEqual(self.client.get(path+'?q=New+29').status_code,200)
        for suffix in ('?page=0','?page=bad','?kind=bogus'):
            self.assertEqual(self.client.get(path+suffix).status_code,400)
        self.assertEqual(self.client.get('/admin/workbooks?status=bogus').status_code,400)

    def test_snapshot_contains_no_authentication_or_unrelated_country_data(self):
        actual=A.workbook_review.importer.import_workbook
        def inspect(path,database,*args,**kwargs):
            with closing(A.sqlite3.connect(database)) as con:
                tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertEqual(tables,{'countries','companies','contacts'})
                self.assertEqual(con.execute('SELECT id FROM countries').fetchall(),[(self.cid,)])
            return actual(path,database,*args,**kwargs)
        with patch.object(A.workbook_review.importer,'import_workbook',side_effect=inspect):
            A.workbook_review.preview(A.ROOT,self.admin,self.row['id'])

    def test_confirmation_and_cancellation_never_mutate_submission(self):
        before=self.snapshot(); initial=A.workbook_submissions.get_submission(A.ROOT,self.admin,self.row['id'])
        baseline=A.workbook_review.preview(A.ROOT,self.admin,self.row['id'])['baseline']
        for action,fields in (('review',{'baseline':baseline}),('reject',{'reason':'Synthetic reason'})):
            response=self.post(action,confirmed='',**fields)
            self.assertEqual(response.status_code,200)
            self.assertIn('Cancel',response.get_data(as_text=True))
            self.assertEqual(self.client.get(self.path).status_code,200)
            self.assertEqual(initial,A.workbook_submissions.get_submission(A.ROOT,self.admin,self.row['id']))
        self.assertEqual(before,self.snapshot())

def sqlite_error(): return A.sqlite3.OperationalError('Synthetic event failure')

if __name__=='__main__': unittest.main()
