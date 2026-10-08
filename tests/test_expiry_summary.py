"""Expiry summary checks with synthetic contact rows only."""
import json, sys, unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from utils.fields import nearest_expiry
from jinja2 import Environment, FileSystemLoader

class ExpirySummaryTest(unittest.TestCase):
    today=date(2026,10,8)

    def row(self,expiry,city='Chennai',state='Tamil Nadu',network='WCA'):
        return {'source_data':json.dumps({'Network Expiry':expiry,'City':city,'State':state,'Network':network})}

    def summary(self,rows,denied=()):
        return nearest_expiry(rows,lambda key:key not in denied,self.today)

    def test_nearest_and_matching_location(self):
        result=self.summary([self.row('2026-12-30','Other','Place'),self.row('2026-12-19')])
        self.assertEqual(result,{'days':72,'location':'Chennai, Tamil Nadu','network':'WCA'})
        self.assertEqual(self.summary([self.row('2026-12-19')])['days'],72)
        self.assertEqual(nearest_expiry([self.row('2026-12-19')],lambda k:True,date(2026,10,9))['days'],71)

    def test_no_upcoming_near_expiry(self):
        for rows in ([],[self.row(None)],[self.row('invalid')],[self.row('2026-10-07')],[self.row('2027-01-07')]):
            with self.subTest(rows=rows): self.assertIsNone(self.summary(rows))

    def test_threshold_today_and_ties(self):
        self.assertEqual(self.summary([self.row('2027-01-06')])['days'],90)
        self.assertEqual(self.summary([self.row('2026-10-08')])['days'],0)
        self.assertEqual(self.summary([self.row('2026-12-19','First',None),self.row('2026-12-19','Second',None)])['location'],'First')

    def test_permissions_and_partial_locations(self):
        rows=[self.row('2026-12-19')]
        self.assertIsNone(self.summary(rows,('x_networkexpiry',)))
        self.assertEqual(self.summary(rows,('city',))['location'],'Tamil Nadu')
        self.assertEqual(self.summary(rows,('state',))['location'],'Chennai')
        self.assertEqual(self.summary(rows,('city','state'))['location'],'')
        self.assertEqual(self.summary([self.row('2026-12-19',None,'nan')])['location'],'')

    def test_philippine_date(self):
        class Clock(datetime):
            @classmethod
            def now(cls,tz):
                self.assertEqual(tz.utcoffset(None).total_seconds(),8*3600)
                return datetime(2026,10,8,16,30,tzinfo=timezone.utc).astimezone(tz)
        with patch('utils.fields.datetime',Clock):
            self.assertEqual(nearest_expiry([self.row('2026-12-19')],lambda k:True)['days'],71)

    def test_shared_rendering_and_escaping(self):
        env=Environment(autoescape=True,loader=FileSystemLoader(str(ROOT/'templates')))
        render=env.get_template('_summary.html').module.expiry_summary
        self.assertEqual(render(None),'')
        text=render(self.summary([self.row('2026-12-19')]))
        self.assertIn('Network: WCA Chennai, Tamil Nadu - <span class="tone-warn">Expires in 72 days</span>',text)
        self.assertNotIn('|',text)
        self.assertNotIn(' - ',render({'days':1,'location':''}))
        self.assertIn('1 day<',render({'days':1,'location':''}))
        self.assertIn('&lt;script&gt;',render({'days':2,'location':'<script>'}))

    def test_network_from_selected_row_and_permissions(self):
        rows=[self.row('2026-12-30',network='WCA'),self.row('2026-12-21','Mumbai','Maharashtra','OLO')]
        result=self.summary(rows)
        self.assertEqual(result,{'days':74,'location':'Mumbai, Maharashtra','network':'OLO'})
        env=Environment(autoescape=True,loader=FileSystemLoader(str(ROOT/'templates')))
        render=env.get_template('_summary.html').module.expiry_summary
        self.assertIn('Network: OLO Mumbai, Maharashtra - <span class="tone-warn">Expires in 74 days</span>',render(result))
        hidden=self.summary(rows,('network',))
        self.assertEqual(hidden['network'],'')
        self.assertNotIn('Network:',render(hidden))
        self.assertNotIn('OLO',render(hidden))
        missing=self.summary([self.row('2026-12-21',None,None,None)])
        self.assertEqual(render(missing),'<span class="expiry-summary"><span class="tone-warn">Expires in 74 days</span></span>')
        self.assertIn('&lt;OLO&gt;',render(self.summary([self.row('2026-12-21',network='<OLO>')])))

if __name__=='__main__': unittest.main()
