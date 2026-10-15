"""Submitter HTTP flow on a disposable application and synthetic database only."""
from pathlib import Path
import io, runpy, unittest
from unittest.mock import patch
from openpyxl import Workbook

state=runpy.run_path(str(Path(__file__).resolve().parent/'test_mfa_enrollment.py'))
A=state['A']; csrf=state['csrf']

class WorkbookRouteTests(unittest.TestCase):
    serial=0
    def setUp(self):
        type(self).serial+=1
        self.name='submitter'+str(self.serial)
        with A.db() as c:
            c.execute('DELETE FROM security_events')
            self.uid=c.execute("INSERT INTO users(username,password_hash,role,status) VALUES(?,?,'FULL_ACCESS','ACTIVE')",
                (self.name,A.generate_password_hash('Synthetic-Upload-Password!'))).lastrowid
            self.country=c.execute('INSERT INTO countries(name) VALUES(?)',('Synthetic country '+str(self.serial),)).lastrowid
            c.execute('INSERT INTO user_workbook_upload_access VALUES(?,?)',(self.uid,self.country))
        self.client=A.app.test_client()
        self.client.post('/login',data={'username':self.name,'password':'Synthetic-Upload-Password!','csrf_token':csrf(self.client,'/login')})

    def workbook(self):
        book=Workbook(); book.active.append(['Company','Country','Street address'])
        book.active.append(['Synthetic Company','Synthetic country '+str(self.serial),'Long synthetic address '*15])
        stream=io.BytesIO(); book.save(stream); stream.seek(0); return stream

    def upload(self,**overrides):
        data={'csrf_token':csrf(self.client,'/workbooks/upload'),'country_id':str(self.country),
              'complete_country':'1','note':'<script>synthetic note</script>','workbook':(self.workbook(),'synthetic.xlsx')}
        data.update(overrides)
        return self.client.post('/workbooks/upload',data=data)

    def test_success_history_detail_download_and_no_live_import(self):
        with A.db() as c:
            before={t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in ('companies','contacts','imports','users')}
        payload=self.workbook().getvalue()
        result=self.upload(workbook=(io.BytesIO(payload),'synthetic.xlsx')); self.assertEqual(result.status_code,302)
        detail=self.client.get(result.location); html=detail.get_data(as_text=True)
        self.assertIn('Pending review',html); self.assertIn('&lt;script&gt;synthetic note&lt;/script&gt;',html)
        self.assertIn('synthetic.xlsx',self.client.get('/workbooks').get_data(as_text=True))
        download=self.client.get(result.location+'/download')
        self.assertEqual(download.status_code,200); self.assertEqual(download.data,payload)
        self.assertIn('no-store',download.headers['Cache-Control'])
        self.assertIn('attachment',download.headers['Content-Disposition'])
        with A.db() as c:
            self.assertEqual(before,{t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in before})
        self.assertNotIn('aria-current="page">',html.split('Directory</span>')[0][-80:])

    def test_form_csrf_country_filename_and_validation_errors(self):
        for token in ('','invalid'):
            self.assertEqual(self.upload(csrf_token=token).status_code,400)
        self.assertEqual(self.upload(country_id='999999').status_code,403)
        self.assertEqual(self.upload(country_id='bad').status_code,400)
        self.assertEqual(self.upload(complete_country='').status_code,400)
        self.assertEqual(self.upload(note='x'*2001).status_code,400)
        self.assertEqual(self.upload(workbook=(self.workbook(),'../bad.xlsx')).status_code,400)
        invalid=self.upload(workbook=(io.BytesIO(b'not a workbook'),'bad.xlsx'))
        self.assertEqual(invalid.status_code,302)
        self.assertIn('Validation failed',self.client.get(invalid.location).get_data(as_text=True))
        for page in ('0','-1','bad','9999999'):
            self.assertEqual(self.client.get('/workbooks?page='+page).status_code,400)

    def test_other_owner_missing_permission_roles_and_sessions(self):
        result=self.upload(); path=result.location
        with A.db() as c:
            c.execute('UPDATE workbook_submissions SET uploader_id=NULL WHERE id=?',(path.split('/')[-1],))
        for url in (path,path+'/download','/workbooks/not-an-id'):
            self.assertEqual(self.client.get(url).status_code,404)
        self.assertNotIn('synthetic.xlsx',self.client.get('/workbooks').get_data(as_text=True))
        for role in ('USER','ADMIN'):
            with A.db() as c: c.execute('UPDATE users SET role=? WHERE id=?',(role,self.uid))
            for url in ('/workbooks','/workbooks/upload',path,path+'/download'):
                self.assertIn(self.client.get(url).status_code,(302,403))
            token=csrf(self.client,'/login')
            self.assertIn(self.client.post('/workbooks/upload',data={'csrf_token':token,'country_id':str(self.country),
                'complete_country':'1','workbook':(self.workbook(),'denied.xlsx')}).status_code,(302,403))
        with A.db() as c:
            c.execute("UPDATE users SET role='FULL_ACCESS' WHERE id=?",(self.uid,))
            c.execute('DELETE FROM user_workbook_upload_access WHERE user_id=?',(self.uid,))
        self.client.post('/login',data={'username':self.name,'password':'Synthetic-Upload-Password!','csrf_token':csrf(self.client,'/login')})
        self.assertEqual(self.client.get('/workbooks').status_code,403)
        self.assertNotIn('>Workbooks</a>',self.client.get('/search').get_data(as_text=True))
        self.assertEqual(A.app.test_client().get('/workbooks').status_code,302)

    def test_checksum_duplicates_storage_failure_and_required_mfa(self):
        payload=self.workbook().getvalue()
        first=self.upload(workbook=(io.BytesIO(payload),'same.xlsx'))
        second=self.upload(workbook=(io.BytesIO(payload),'same.xlsx'))
        self.assertIn('matches an earlier upload',self.client.get(second.location).get_data(as_text=True))
        row=A.workbook_submissions.get_submission(A.ROOT,self.uid,first.location.split('/')[-1])
        (A.ROOT/'workbook_submissions'/(row['id']+'.xlsx')).write_bytes(b'synthetic tamper')
        self.assertEqual(self.client.get(first.location+'/download').status_code,409)
        with patch.object(A.workbook_submissions,'create_submission',side_effect=OSError('private path')):
            response=self.upload(); self.assertEqual(response.status_code,400)
            self.assertNotIn('private path',response.get_data(as_text=True))
        with A.db() as c:
            c.execute('INSERT INTO user_mfa(user_id,required) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET required=1',(self.uid,))
        self.assertEqual(self.client.get('/workbooks').status_code,302)

    def test_limits_pagination_and_disabled_account(self):
        with patch.dict(A.app.config,MAX_CONTENT_LENGTH=2048):
            self.assertEqual(self.upload().status_code,413)
        with patch.dict(A.app.config['SECURITY_RATE_LIMITS'],{'import':1}):
            self.assertEqual(self.upload().status_code,302)
            self.assertEqual(self.upload().status_code,429)
        # Populate history with metadata only; no extra workbook processing is needed.
        with A.db() as c:
            row=dict(c.execute('SELECT * FROM workbook_submissions WHERE uploader_id=?',(self.uid,)).fetchone())
            for index in range(21):
                row.update(id=f'{self.uid:016x}{index:016x}',sha256=None,duplicate_of=None,
                           original_filename=f'page-{index:02d}.xlsx')
                c.execute('INSERT INTO workbook_submissions('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
        self.assertIn('>Next</a>',self.client.get('/workbooks').get_data(as_text=True))
        self.assertIn('>Previous</a>',self.client.get('/workbooks?page=2').get_data(as_text=True))
        with A.db() as c: c.execute("UPDATE users SET status='DISABLED' WHERE id=?",(self.uid,))
        self.assertEqual(self.client.get('/workbooks').status_code,302)

if __name__=='__main__': unittest.main()
