"""Synthetic search statuses and template rendering; no application startup."""
import json, sys, unittest
from datetime import date
from pathlib import Path
from jinja2 import Environment, ChoiceLoader, DictLoader, FileSystemLoader
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from utils.status import search_status

class SearchSummaryTest(unittest.TestCase):
    def status(self,kyc='Pending',expiry='2026-12-21',denied=()):
        return search_status(json.dumps({'Agent Status':'Active','KYC Status':kyc,'Network Expiry':'2026-10-09'}),
            [{'source_data':json.dumps({'Network Expiry':expiry})}],lambda k:k not in denied,date(2026,10,8))

    def test_pending_and_nearest_contact_expiry(self):
        self.assertEqual([f['text'] for f in self.status()['flags']],['Active','KYC Pending','Network expires in 74 days'])

    def test_omissions_and_permissions(self):
        for kyc in (None,'Approved','Rejected','Done'):
            for expiry in (None,'invalid','2026-10-07','2027-04-01'):
                self.assertEqual([f['text'] for f in self.status(kyc,expiry)['flags']],['Active'])
        self.assertEqual([f['text'] for f in self.status(denied=('x_kycstatus','x_networkexpiry'))['flags']],['Active'])
        self.assertIn('KYC Pending',[f['text'] for f in self.status(' pending ')['flags']])

    def test_search_rendering(self):
        env=Environment(autoescape=True,loader=ChoiceLoader([
            DictLoader({'base.html':'{% block content %}{% endblock %}'}),FileSystemLoader(str(ROOT/'templates'))]))
        env.filters.update(hl=lambda value,q:value,first_email=lambda value:value,tel=lambda value:value)
        env.globals['url_for']=lambda endpoint,**kw:'/company/'+str(kw['company_id']) if endpoint=='company' else '/search'
        row=dict(id=975,company_name='Synthetic company',agent_id='TEST975',alias=None,city='HiddenCity',
                 state='HiddenState',country='India',code='IN',network='OLO',status=self.status(),contacts=6,matched=6,preview_id=1)
        html=env.get_template('search.html').render(q='Synthetic',country='',rows=[row],previews={1:dict(name='Contact name',email=None,phone=None)},
                fields=['company_name','city','state','country','network','name'],total=1,start=1,end=1,pages=1,page=1)
        for text in ('Active','KYC Pending','Network expires in 74 days','Contact name','/company/975'):
            self.assertIn(text,html)
        for text in ('HiddenCity','HiddenState','OLO','class="cc"','Network:','expiry-summary'):
            self.assertNotIn(text,html)

if __name__=='__main__': unittest.main()
