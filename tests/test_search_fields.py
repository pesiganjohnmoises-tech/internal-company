"""Search exclusions and access checks in a disposable app copy with synthetic SQLite only."""
import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch
from flask import template_rendered

state = runpy.run_path(str(Path(__file__).resolve().parent / 'test_mfa_enrollment.py'))
A = state['A']
A.app.config.update(MFA_ENABLED=False, MFA_ENROLLMENT_ENABLED=False)


class SearchFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ids = {}
        cls.password = 'Synthetic-Search-Password!'
        with A.db() as c:
            cls.india = c.execute("INSERT INTO countries(name) VALUES('India')").lastrowid
            cls.other = c.execute("INSERT INTO countries(name) VALUES('Synthetic Other')").lastrowid
            def company(key, company_name, country=None, **values):
                cid = c.execute('INSERT INTO companies(country_id,company_name,agent_id,city,state,network,source_data) VALUES(?,?,?,?,?,?,?)',
                    (country or cls.india, company_name, values.get('agent_id'), 'CityOnly', 'StateOnly', 'NetworkOnly', json.dumps({'Alias': values.get('alias', ''), 'Network': 'NetworkOnly'}))).lastrowid
                cls.ids[key] = cid
                contact = {f: values.get(f, '') for f in ('name', 'contact_type', 'job_position', 'email', 'phone', 'landline_no', 'address')}
                c.execute('INSERT INTO contacts(company_id,name,contact_type,job_position,email,phone,landline_no,address,source_data) VALUES(?,?,?,?,?,?,?,?,?)', (cid, *contact.values(), json.dumps({"Network": "NetworkOnly"})))
                return cid
            company('quiet', 'Quiet Company')
            company('india-name', 'India Trading', country=cls.other)
            for f in ('company_name', 'agent_id', 'alias', 'contact_type', 'name', 'job_position', 'email', 'phone', 'landline_no', 'address'):
                cls_token = 'Token' + f.replace('_', '')
                company(f, cls_token if f == 'company_name' else 'Retained ' + f, **({f: cls_token} if f != 'company_name' else {}))
            company('combined', 'Combined Company', name='ComboPerson', email='ComboEmail@example.invalid')
            cls.paged = [company('paged' + str(n), 'PagerMatch %02d' % n) for n in range(25)]
            cls.users = {}
            for role in ('ADMIN', 'FULL_ACCESS', 'USER'):
                uid = c.execute('INSERT INTO users(username,password_hash,role,status) VALUES(?,?,?,?)',
                    ('search_' + role, A.generate_password_hash(cls.password), role, 'ACTIVE')).lastrowid
                cls.users[role] = uid
            c.execute('INSERT INTO user_country_access(user_id,country_id,all_companies) VALUES(?,?,0)', (cls.users['USER'], cls.india))
            c.execute('INSERT INTO user_company_access(user_id,company_id) VALUES(?,?)', (cls.users['USER'], cls.ids['quiet']))
        cls.clients = {}
        for role in cls.users:
            client = A.app.test_client()
            result = client.post('/login', data={'username': 'search_' + role, 'password': cls.password, 'csrf_token': state['csrf'](client, '/login')})
            assert result.status_code == 302
            cls.clients[role] = client

    def search(self, q='', role='FULL_ACCESS', **args):
        contexts = []
        def capture(sender, template, context, **extra):
            if template.name == 'search.html': contexts.append(context)
        with template_rendered.connected_to(capture, A.app):
            response = self.clients[role].get('/search', query_string={'q': q, **args})
        self.assertEqual(response.status_code, 200)
        return contexts[0], response.get_data(as_text=True)

    def test_excluded_fields_do_not_match_for_any_role(self):
        for role in self.clients:
            for term in ('CityOnly', 'StateOnly', 'NetworkOnly', 'Synthetic Other'):
                context, _ = self.search(term, role)
                self.assertEqual(context['total'], 0, (role, term))

    def test_country_only_block_and_longer_company_search(self):
        with A.db() as c:
            c.execute('UPDATE contacts SET address=? WHERE company_id=?', ('Street, India', self.ids['quiet']))
        for role in self.clients:
            for query in ('India', '  iNdIa  ', 'Synthetic   Other'):
                context, html = self.search(query, role, page=3)
                self.assertEqual((context['total'], context['pages'], context['page'], context['rows']), (0, 1, 1, []))
                self.assertTrue(context['country_only'])
                self.assertIn('Country names alone cannot be searched', html)
        context, _ = self.search('India Trading')
        self.assertEqual([r['id'] for r in context['rows']], [self.ids['india-name']])
        self.assertEqual(self.search('India Trading', 'USER')[0]['total'], 0)
        self.assertEqual(self.search('India', country='India')[0]['total'], 0)
        with A.db() as c:
            c.execute('UPDATE companies SET company_name=? WHERE id=?', ('Aegon Logistics India', self.ids['india-name']))
        try:
            context, _ = self.search('Aegon Logistics India')
            self.assertEqual([r['id'] for r in context['rows']], [self.ids['india-name']])
        finally:
            with A.db() as c:
                c.execute('UPDATE companies SET company_name=? WHERE id=?', ('India Trading', self.ids['india-name']))

    def test_all_retained_fields_still_match(self):
        for field in ('company_name', 'agent_id', 'alias', 'contact_type', 'name', 'job_position', 'email', 'phone', 'landline_no'):
            context, _ = self.search('Token' + field.replace('_', ''))
            self.assertEqual([r['id'] for r in context['rows']], [self.ids[field]], field)

    def test_combined_terms_keep_existing_and_semantics(self):
        context, _ = self.search('ComboPerson ComboEmail')
        self.assertEqual([r['id'] for r in context['rows']], [self.ids['combined']])
        for query in ('ComboPerson India', 'ComboPerson CityOnly', 'ComboPerson NetworkOnly', 'ComboPerson StateOnly'):
            self.assertEqual(self.search(query)[0]['total'], 0, query)

    def test_counts_and_pagination_share_exclusions(self):
        first, html = self.search('PagerMatch', page=1)
        second, _ = self.search('PagerMatch', page=2)
        self.assertEqual((first['total'], second['total'], first['pages'], second['pages']), (25, 25, 2, 2))
        self.assertEqual((len(first['rows']), len(second['rows'])), (20, 5))
        self.assertEqual({r['id'] for r in first['rows'] + second['rows']}, set(self.paged))
        self.assertIn('page=2', html)
        excluded, _ = self.search('NetworkOnly', page=2)
        self.assertEqual((excluded['total'], excluded['pages'], excluded['rows']), (0, 1, []))

    def test_permissions_filters_and_display_remain(self):
        context, _ = self.search('Quiet', 'USER')
        self.assertEqual([r['id'] for r in context['rows']], [self.ids['quiet']])
        self.assertEqual(self.search('PagerMatch', 'USER')[0]['total'], 0)
        self.assertEqual(self.search('', 'USER', country='India')[0]['total'], 0)
        self.assertEqual(self.search('', 'USER', country='Synthetic Other')[0]['total'], 0)
        self.assertGreater(self.search('', country='India')[0]['total'], 25)
        self.assertEqual(self.search('India', country='India')[0]['total'], 0)
        with patch.object(A, 'visible_fields', return_value=['email']):
            self.assertEqual(self.search('Tokenname')[0]['total'], 0)
            self.assertEqual(self.search('Tokenemail')[0]['total'], 1)
            self.assertEqual(self.search('Tokenalias')[0]['total'], 1)
        row = context['rows'][0]
        self.assertEqual((row['city'], row['state'], row['network'], row['country']), ('CityOnly', 'StateOnly', 'NetworkOnly', 'India'))
        detail = self.clients['USER'].get('/company/' + str(self.ids['quiet'])).get_data(as_text=True)
        for text in ('CityOnly', 'StateOnly', 'NetworkOnly', 'India'): self.assertIn(text, detail)

    def test_addresses_excluded_with_counts_pagination_and_display(self):
        with A.db() as c:
            companies = [tuple(r) for r in c.execute('SELECT id,company_name,address FROM companies')]
            contacts = [tuple(r) for r in c.execute('SELECT id,address,source_data FROM contacts')]
            c.execute('UPDATE companies SET address=?', ('Street in Mumbai',))
            c.execute('UPDATE contacts SET address=?,source_data=?', ('Street in Mumbai', json.dumps({'Street': 'Street in Mumbai', 'Network': 'NetworkOnly'})))
            c.execute('UPDATE companies SET company_name=? WHERE id=?', ('Mumbai Trading', self.ids['india-name']))
        try:
            for role in self.clients:
                context, _ = self.search('Tokenaddress', role)
                self.assertEqual(context['total'], 0)
                context, _ = self.search('Mumbai', role, page=2)
                expected = [] if role == 'USER' else [self.ids['india-name']]
                self.assertEqual([r['id'] for r in context['rows']], expected)
                self.assertEqual((context['total'], context['pages'], context['page']), (len(expected), 1, 1))
                self.assertEqual(self.search('Quiet Mumbai', role)[0]['total'], 0)
                self.assertEqual(self.search('PagerMatch Mumbai', role)[0]['total'], 0)
            self.assertEqual(self.search('Mumbai', country='India')[0]['total'], 0)
            self.assertEqual(self.search('Mumbai Trading')[0]['total'], 1)
            self.assertEqual(self.search('Quiet', 'USER')[0]['total'], 1)
            detail = self.clients['USER'].get('/company/' + str(self.ids['quiet'])).get_data(as_text=True)
            self.assertIn('Street in Mumbai', detail)
            with A.db() as c:
                self.assertEqual(c.execute('SELECT address FROM companies WHERE id=?', (self.ids['quiet'],)).fetchone()[0], 'Street in Mumbai')
                self.assertEqual(c.execute('SELECT address FROM contacts WHERE company_id=?', (self.ids['quiet'],)).fetchone()[0], 'Street in Mumbai')
        finally:
            with A.db() as c:
                c.executemany('UPDATE companies SET company_name=?,address=? WHERE id=?', [(name, address, cid) for cid, name, address in companies])
                c.executemany('UPDATE contacts SET address=?,source_data=? WHERE id=?', [(address, source, cid) for cid, address, source in contacts])

    def test_minimum_length_for_all_roles_and_filters(self):
        for role in self.clients:
            for query in ('a', 'ba', 'dor', 'naru', 'Q u i e', '  Quie  ', 'Q\tu\ni e'):
                for args in ({'page': 3}, {'country': 'India', 'offset': 40}):
                    context, html = self.search(query, role, **args)
                    self.assertEqual((context['total'], context['rows'], context['page'], context['pages']), (0, [], 1, 1))
                    self.assertTrue(context['short_query'])
                    self.assertIn('Enter at least 5 characters to search.', html)
                    self.assertNotIn('class="pager"', html)

    def test_exactly_five_characters_and_whitespace(self):
        for role in self.clients:
            for query in ('Quiet', '  Quiet  ', 'Q u i e t', 'Q\tu\ni e t'):
                context, _ = self.search(query, role)
                self.assertFalse(context['short_query'])
                self.assertEqual([r['id'] for r in context['rows']], [self.ids['quiet']])
        context, _ = self.search('Quiet', 'USER', country='Synthetic Other')
        self.assertEqual(context['total'], 0)

    def test_blank_search_preserves_landing_and_filter(self):
        for query in ('', '   ', '\t\n'):
            context, html = self.search(query)
            self.assertFalse(context['short_query'])
            self.assertEqual(context['rows'], [])
            self.assertIn('class="home"', html)
            self.assertIn('id="search-hint"', html)
            self.assertIn('aria-describedby="search-hint"', html)
            self.assertGreater(self.search(query, country='India')[0]['total'], 25)

    def test_placeholder_both_search_states(self):
        for query in ('', 'Quiet'):
            _, html = self.search(query)
            self.assertIn('placeholder="Agent name, agent ID or contact', html)
            self.assertNotIn('agent ID, city, network', html)


if __name__ == '__main__':
    unittest.main()
