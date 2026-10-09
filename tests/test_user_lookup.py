"""USER lookup policy checks using disposable code and synthetic SQLite/files only."""
from pathlib import Path
import runpy
import unittest

state = runpy.run_path(str(Path(__file__).resolve().parent / 'test_search_fields.py'))
A = state['A']


class UserLookupTests(unittest.TestCase):
    search = state['SearchFieldTests'].search

    @classmethod
    def setUpClass(cls):
        Base = state['SearchFieldTests']
        Base.setUpClass()
        cls.clients = dict(Base.clients)
        cls.ids = {}
        with A.db() as c:
            cls.alpha = c.execute("INSERT INTO countries(name) VALUES('Lookup Alpha')").lastrowid
            cls.beta = c.execute("INSERT INTO countries(name) VALUES('Lookup Beta')").lastrowid
            cls.uid = c.execute('INSERT INTO users(username,password_hash,role,status) VALUES(?,?,?,?)', ('lookup_user', A.generate_password_hash(Base.password), 'USER', 'ACTIVE')).lastrowid
            for country in (cls.alpha, cls.beta):
                c.execute('INSERT INTO user_country_access(user_id,country_id,all_companies) VALUES(?,?,1)', (cls.uid, country))
            def add(key, name, country=None, agent_id=None):
                cid = c.execute('INSERT INTO companies(country_id,company_name,agent_id) VALUES(?,?,?)', (country or cls.alpha, name, agent_id)).lastrowid
                cls.ids[key] = cid
                c.execute('INSERT INTO contacts(company_id,name,email) VALUES(?,?,?)', (cid, 'Lookup Person', 'lookup@example.invalid'))
                return cid
            add('one', 'OneLookup Company')
            for n in range(10): add('ten'+str(n), 'TenLookup %02d' % n)
            for n in range(11): add('eleven'+str(n), 'ElevenLookup %02d' % n, cls.beta if n == 10 else cls.alpha)
            add('exact', 'IdLookup Exact', agent_id='MATCH001')
            for n in range(11): add('partial'+str(n), 'IdLookup Partial %02d' % n, agent_id='MATCH001%02d' % n)
            add('dup1', 'Duplicate Exact One', agent_id='DUPID001')
            add('dup2', 'Duplicate Exact Two', cls.beta, 'DUPID001')
            for n in range(11): add('manydup'+str(n), 'Duplicate Many %02d' % n, agent_id='DUPMANY')
            add('denied', 'Hidden Exact', Base.other, 'DENIED001')
            for n in range(11): add('deniedpartial'+str(n), 'Permitted Partial %02d' % n, agent_id='DENIED001%02d' % n)
            add('shortid', 'Short Identifier', agent_id='ID12')
            for n in range(25): c.execute('INSERT INTO contacts(company_id,name) VALUES(?,?)', (cls.ids['one'], 'Extra Contact'))
        cls.clients['USER'] = A.app.test_client()
        response = cls.clients['USER'].post('/login', data={'username': 'lookup_user', 'password': Base.password, 'csrf_token': state['state']['csrf'](cls.clients['USER'], '/login')})
        assert response.status_code == 302

    def test_distinct_thresholds_and_many_contacts(self):
        for query, count in (('NoMatch', 0), ('OneLookup', 1), ('TenLookup', 10), ('ElevenLookup', 0)):
            context, html = self.search(query, 'USER')
            self.assertEqual((context['total'], len(context['rows'])), (count, count))
            self.assertEqual(context['too_many'], query == 'ElevenLookup')
            if context['too_many']:
                self.assertIn('Too many matches. Enter a more specific company name, agent ID, or contact detail.', html)
                self.assertNotIn('class="result-row"', html)
            if query == 'OneLookup': self.assertEqual(context['rows'][0]['contacts'], 26)

    def test_pagination_and_offset_cannot_bypass(self):
        for args in ({'page': 2}, {'page': 999}, {'offset': 20}, {'offset': 999}):
            context, html = self.search('ElevenLookup', 'USER', **args)
            self.assertEqual((context['total'], context['rows'], context['page'], context['pages']), (0, [], 1, 1))
            self.assertNotIn('class="pager"', html)
            self.assertEqual(self.search('TenLookup', 'USER', **args)[0]['total'], 10)

    def test_exact_ids_partial_duplicates_and_permissions(self):
        context, _ = self.search('match001', 'USER')
        self.assertEqual([r['id'] for r in context['rows']], [self.ids['exact']])
        self.assertEqual(self.search('MATCH', 'USER')[0]['total'], 0)
        context, _ = self.search('DUPID001', 'USER')
        self.assertEqual({r['id'] for r in context['rows']}, {self.ids['dup1'], self.ids['dup2']})
        self.assertEqual([r['id'] for r in self.search('DUPID001', 'USER', country='Lookup Alpha')[0]['rows']], [self.ids['dup1']])
        self.assertEqual(self.search('MATCH0010', 'USER')[0]['total'], 10)
        self.assertEqual(self.search('DENIED001', 'USER', country='Synthetic Other')[0]['total'], 0)
        self.assertTrue(self.search('DUPMANY', 'USER')[0]['too_many'])
        self.assertTrue(self.search('DENIED001', 'USER')[0]['too_many'])
        self.assertEqual(self.clients['USER'].get('/company/'+str(self.ids['denied'])).status_code, 404)
        self.assertEqual(self.clients['USER'].get('/company/'+str(self.ids['exact'])).status_code, 200)
        self.assertEqual(self.search('MATCH001', 'USER', country='Lookup Beta')[0]['total'], 0)
        self.assertEqual(self.search('ID12', 'USER')[0]['total'], 0)
        # A country grant without the company grant must not authorize exact-ID access.
        with A.db() as c: c.execute('UPDATE user_country_access SET all_companies=0 WHERE user_id=? AND country_id=?', (self.uid, self.alpha))
        try:
            self.assertEqual(self.search('MATCH001', 'USER')[0]['total'], 0)
        finally:
            with A.db() as c: c.execute('UPDATE user_country_access SET all_companies=1 WHERE user_id=? AND country_id=?', (self.uid, self.alpha))

    def test_country_filters_and_existing_query_gates(self):
        for query in ('', '  '):
            context, html = self.search(query, 'USER', country='Lookup Alpha', offset=40)
            self.assertTrue(context['lookup_required'])
            self.assertEqual((context['total'], context['rows'], context['page']), (0, [], 1))
            self.assertIn('Country filters can narrow your search', html)
        self.assertEqual(self.search('ElevenLookup', 'USER', country='Lookup Alpha')[0]['total'], 10)
        for query in ('ar', 'Lookup Alpha', 'CityOnly', 'NetworkOnly', 'Tokenaddress'):
            self.assertEqual(self.search(query, 'USER')[0]['total'], 0)
        context, html = self.search('', 'USER')
        self.assertEqual(context['rows'], [])
        self.assertIn('class="home"', html)

    def test_admin_full_access_unchanged(self):
        for role in ('ADMIN', 'FULL_ACCESS'):
            self.assertEqual(self.search('ElevenLookup', role)[0]['total'], 11)
            self.assertEqual(self.search('MATCH001', role)[0]['total'], 12)
            self.assertGreater(self.search('', role, country='Lookup Alpha')[0]['total'], 10)
            context, _ = self.search('MATCH001', role, page=2)
            self.assertEqual(context['total'], 12)
            context, _ = self.search('PagerMatch', role, page=2)
            self.assertEqual((context['total'], context['pages'], len(context['rows'])), (25, 2, 5))

    def test_bulk_routes_remain_admin_only(self):
        for role in ('USER', 'FULL_ACCESS'):
            for url in ('/admin/export/companies.csv', '/admin/export/contacts.csv', '/admin/imports/download/synthetic.xlsx'):
                self.assertEqual(self.clients[role].get(url).status_code, 403)
        for kind in ('companies', 'contacts'):
            response = self.clients['ADMIN'].get('/admin/export/'+kind+'.csv')
            self.assertEqual(response.status_code, 200)
            self.assertIn('attachment', response.headers['Content-Disposition'])


if __name__ == '__main__': unittest.main()
