"""Synthetic company-route checks without importing app.py or touching directory.db."""
import ast, json, sqlite3, sys, unittest
from contextlib import contextmanager
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from flask import Flask, abort, render_template, request, url_for
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader
from utils.fields import build_company_detail


class SalesVisibilityTest(unittest.TestCase):
    def setUp(self):
        self.con=sqlite3.connect(":memory:")
        self.con.row_factory=sqlite3.Row
        self.addCleanup(self.con.close)
        self.con.executescript("""
            CREATE TABLE user_field_access(user_id INTEGER, field_name TEXT);
            CREATE TABLE countries(id INTEGER, name TEXT);
            CREATE TABLE companies(id INTEGER, country_id INTEGER, company_name TEXT,
                source_data TEXT, network TEXT, city TEXT, state TEXT, address TEXT,
                agent_id TEXT, source_file TEXT, source_row INTEGER);
            CREATE TABLE contacts(id INTEGER, company_id INTEGER, source_data TEXT,
                name TEXT, contact_type TEXT, job_position TEXT, email TEXT, phone TEXT,
                landline_no TEXT, address TEXT, source_row INTEGER);
            CREATE TABLE user_country_access(user_id INTEGER, country_id INTEGER, all_companies INTEGER);
            CREATE TABLE user_company_access(user_id INTEGER, company_id INTEGER);
            INSERT INTO countries VALUES(1,'Test country');
            INSERT INTO user_country_access VALUES(1,1,1);
        """)
        self.user={"id":1,"role":"USER"}
        self.app=Flask(__name__)
        self.app.config['TESTING']=True
        self.app.context_processor(lambda:{"user":self.user})
        self.app.jinja_loader=ChoiceLoader([
            DictLoader({"base.html":"{% block content %}{% endblock %}"}),
            FileSystemLoader(str(ROOT/"templates"))])

        @contextmanager
        def db():
            with self.con:
                yield self.con

        # Execute only the relevant definitions: app.py startup initializes real data.
        names={"FIELD_COLUMNS","FIELD_LABELS","SALES_FIELDS","visible_fields","access_sql","company"}
        tree=ast.parse((ROOT/"app.py").read_text(encoding="utf-8"))
        nodes=[]
        for node in tree.body:
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in names for t in node.targets):
                nodes.append(node)
            elif isinstance(node,ast.FunctionDef) and node.name in names:
                node.decorator_list=[]
                nodes.append(node)
        namespace=dict(db=db,current_user=lambda:self.user,request=request,url_for=url_for,
                       abort=abort,render_template=render_template,build_company_detail=build_company_detail,
                       agent_status=lambda raw:{"flags":[]},country_code=lambda country:"XX",
                       source_name=lambda path:"")
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(ROOT/"app.py"),"exec"),namespace)
        self.sales=namespace["SALES_FIELDS"]
        self.app.add_url_rule('/company/<int:company_id>',view_func=namespace['company'])
        self.app.add_url_rule('/search',endpoint='search',view_func=lambda:"")
        self.values=dict(zip(self.sales,["KG synthetic","PC synthetic","TI synthetic","KW synthetic"]))
        self.source=dict(zip(["KG Sales","PC Sales","TI Sales","KW Sales"],self.values.values()))
        self.con.execute("INSERT INTO companies VALUES(844,1,'Synthetic company',?,NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
                         (json.dumps(self.source),))
        for n in range(12):
            self.con.execute("INSERT INTO contacts(id,company_id,name,source_data) VALUES(?,844,'Synthetic agent','{}')",(n+1,))
        self.client=self.app.test_client()

    def render(self,grants=()):
        self.con.execute("DELETE FROM user_field_access")
        self.con.executemany("INSERT INTO user_field_access VALUES(1,?)",[(key,) for key in grants])
        response=self.client.get('/company/844')
        self.assertEqual(response.status_code,200)
        html=response.get_data(as_text=True)
        header=html.split('<dl class="key-facts">',1)[1].split('</dl>',1)[0]
        self.assertIn('<dt>Contacts:</dt><dd>12</dd>',header)
        return html,header

    def assert_sales(self,grants):
        html,header=self.render(grants)
        for key,label in self.sales.items():
            expected=key in grants or self.user['role'] in ('ADMIN','FULL_ACCESS')
            with self.subTest(role=self.user['role'],grants=grants,sales=key):
                self.assertEqual(f'<dt>{label}:</dt><dd>{self.values[key]}</dd>' in header,expected)
                self.assertEqual(self.values[key] in html,expected)

    def test_each_individual_admin_grant(self):
        for key in self.sales:
            self.assert_sales([key])

    def test_no_grants_and_all_grants(self):
        self.assert_sales([])
        self.assert_sales(list(self.sales))

    def test_header_labels_and_stored_sales_value(self):
        from datetime import datetime, timezone, timedelta
        today=datetime.now(timezone(timedelta(hours=8))).date()
        self.source['KG Sales']='Kharla (Plus agent)'
        self.con.execute('UPDATE companies SET source_data=?',(json.dumps(self.source),))
        row={'Network':'OLO','Network Expiry':(today+timedelta(days=74)).isoformat(),
             'City':'Mumbai','State':'Maharashtra'}
        self.con.execute('UPDATE contacts SET source_data=? WHERE id=1',(json.dumps(row),))
        _,header=self.render(['x_kgsales'])
        self.assertIn('<dt>Kargosmart Sales:</dt><dd>Kharla (Plus agent)</dd>',header)
        self.assertIn('<dt>Network:</dt><dd>',header)
        self.assertEqual(header.count('Network:'),1)
        self.assertIn('OLO Mumbai, Maharashtra - <span class="tone-warn">Expires in 74 days</span>',header)
        self.con.execute("UPDATE contacts SET source_data='{}'")
        _,header=self.render(['x_kgsales'])
        self.assertNotIn('Network:',header)

    def test_revoked_grants(self):
        self.assert_sales(list(self.sales))
        self.assert_sales([])

    def test_privileged_roles(self):
        for role in ('ADMIN','FULL_ACCESS'):
            self.user['role']=role
            self.assert_sales([])

    def test_empty_sales_are_omitted(self):
        for header in self.source:
            with self.subTest(header=header):
                source=dict(self.source); source[header]=''
                self.con.execute('UPDATE companies SET source_data=?',(json.dumps(source),))
                _,facts=self.render(list(self.sales))
                key='x_'+header.lower().replace(' ','')
                self.assertNotIn(self.sales[key],facts)
                for other in self.sales:
                    if other!=key: self.assertIn(self.values[other],facts)

    def test_company_access_still_required(self):
        self.con.execute('DELETE FROM user_country_access')
        response=self.client.get('/company/844')
        self.assertEqual(response.status_code,404)
        for value in self.values.values(): self.assertNotIn(value,response.get_data(as_text=True))


if __name__=='__main__': unittest.main()
