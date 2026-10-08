"""Synthetic Contact header/column checks without application startup."""
import sys, json, unittest
from pathlib import Path
from html.parser import HTMLParser
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from utils.fields import build_company_detail
from jinja2 import Environment, ChoiceLoader, DictLoader, FileSystemLoader

class ContactTableParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.inside=False; self.section=None; self.rows=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='table' and attrs.get('id')=='contact-cards': self.inside=True
        if not self.inside: return
        if tag in ('thead','tbody'): self.section=tag
        if tag=='tr': self.rows.append([self.section,[]])
        if tag in ('th','td'): self.rows[-1][1].append([tag,attrs.get('data-label'), ''])
    def handle_data(self,data):
        if self.inside and self.rows and self.rows[-1][1]: self.rows[-1][1][-1][2]+=data
    def handle_endtag(self,tag):
        if tag=='table': self.inside=False

class ContactLayoutTest(unittest.TestCase):
    def test_headers_match_rows_with_optional_columns(self):
        for role in ('USER','ADMIN'):
            for denied in ((),('contact_type','email','address','x_notes')):
                with self.subTest(role=role,denied=denied):
                    source={'Contact Type':'Accounts','Name':'Synthetic','Job Position':'Pricing Specialist',
                            'Email':'long.address@example.test','Phone':'+91 123456789','Street':'Long street '*30,'Notes':'Long note '*30}
                    company=dict(source_data=json.dumps(source),address='Company street',network=None,id=845,company_name='Synthetic',country='Test')
                    contacts=[dict(id=i,source_data=json.dumps(source),name='Long synthetic name '+str(i),contact_type=kind,
                        job_position='Long job title '*8,email='long.address@example.test',phone='+91 123456789',
                        landline_no=None,address='Long street '*30,source_row=i+2) for i,kind in enumerate(('Accounts','Management'),1)]
                    detail=build_company_detail(company,contacts,lambda key:key not in denied)
                    env=Environment(autoescape=True,loader=ChoiceLoader([DictLoader({'base.html':'{% block content %}{% endblock %}'}),FileSystemLoader(str(ROOT/'templates'))]))
                    html=env.get_template('company.html').render(company=company,detail=detail,fields=['company_name'],user={'role':role},sales_labels={},back_url='/search')
                    parser=ContactTableParser(); parser.feed(html)
                    self.assertEqual([r[0] for r in parser.rows],['thead','tbody','tbody'])
                    headers=[c[2].strip() for c in parser.rows[0][1]]
                    for _,cells in parser.rows[1:]:
                        self.assertEqual([c[1] for c in cells],headers)
                        self.assertTrue(all(c[0]=='td' for c in cells))
                    self.assertIn('Long synthetic name 1',html)
                    if 'email' not in denied:
                        self.assertIn('mailto:long.address@example.test',html)
                        self.assertIn('data-copy="long.address@example.test"',html)

if __name__=='__main__': unittest.main()
