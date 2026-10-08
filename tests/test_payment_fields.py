"""Focused Payment checks using synthetic SQLite rows; no application startup."""
import json, sqlite3, sys, unittest
from contextlib import closing
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from utils.fields import build_company_detail

class PaymentFieldsTest(unittest.TestCase):
    def detail(self,city=" Mumbai ",state=" Maharashtra ",source=None,denied=(),sources=None,addresses=None):
        with closing(sqlite3.connect(":memory:")) as con:
            con.row_factory=sqlite3.Row
            row=con.execute("SELECT ? source_data, ? city, ? state, ? address, ? network, 'Synthetic agent' company_name, 'Test country' country",
                            (json.dumps(source or {}),city,state,"Stored street","Stored network")).fetchone()
            data=dict(source or {}); data.update(City=city,State=state)
            contacts=[dict(id=i+1,source_data=json.dumps(s),name="Test contact",contact_type=None,
                           job_position=None,email=None,phone=None,landline_no=None,address=addresses[i] if addresses else None,source_row=i+2)
                      for i,s in enumerate(sources if sources is not None else [data])]
            detail=build_company_detail(row,contacts,lambda k:k not in denied)
            self.assertEqual(len(detail["contacts"]),len(contacts))
            return next(s for s in detail["sections"] if s["id"]=="payment")

    def test_order_and_stored_values(self):
        section=self.detail(source={"Payment Terms":"Credit","Network":"Stale source network",
                                    "Network Expiry":"Apr 08, 2027","Street":"Stale source street",
                                    "KYC Status":"Pending","Mapping Status":"Done"},addresses=["Actual contact street"])
        fields=section["rows"][0]["fields"]
        self.assertEqual([f["label"] for f in fields],["Payment Terms","Network","Network Expiry","Street address","City and state","KYC Status","Mapping Status"])
        self.assertEqual([f["value"]["text"] for f in fields],
                         ["Credit","Stale source network","8 Apr 2027","Actual contact street","Mumbai, Maharashtra","Pending","Done"])

    def test_missing_values_and_partial_locations(self):
        fields={f["key"]:f for f in self.detail(city="nan",state=None)["rows"][0]["fields"]}
        for key in ("x_paymentterms","x_networkexpiry","address","city_state","x_kycstatus","x_mappingstatus"):
            self.assertTrue(fields[key]["value"]["empty"])
        for city,state,expected in (("Mumbai",None,"Mumbai"),(None,"Maharashtra","Maharashtra")):
            fields={f["key"]:f for f in self.detail(city=city,state=state)["rows"][0]["fields"]}
            self.assertEqual(fields["city_state"]["value"]["text"],expected)

    def test_permissions(self):
        fields={f["key"]:f for f in self.detail(denied=("state","network","x_kycstatus"))["rows"][0]["fields"]}
        self.assertNotIn("network",fields)
        self.assertNotIn("x_kycstatus",fields)
        self.assertEqual(fields["city_state"]["value"]["text"],"Mumbai")
        fields={f["key"]:f for f in self.detail(denied=("city","state"))["rows"][0]["fields"]}
        self.assertNotIn("city_state",fields)
        section=self.detail(denied=("address",),addresses=["Restricted street"])
        self.assertNotIn("address",[f["key"] for f in section["fields"]])
        self.assertNotIn("address",[f["key"] for f in section["rows"][0]["fields"]])

    def test_distinct_rows_and_no_company_fallback(self):
        section=self.detail(sources=[{"Association":f"Network {i}","Town":f"City {i}","Province":"State",
                                      "Payment Terms":str(i)} for i in range(12)],addresses=[f"Street {i}" for i in range(12)])
        self.assertEqual(len(section["rows"]),12)
        self.assertEqual(len({r["id"] for r in section["rows"]}),12)
        for i,row in enumerate(section["rows"]):
            fields={f["key"]:f for f in row["fields"]}
            self.assertEqual(fields["network"]["value"]["text"],f"Network {i}")
            self.assertEqual(fields["city_state"]["value"]["text"],f"City {i}, State")
            self.assertEqual(fields["address"]["value"]["text"],f"Street {i}")
        empty=self.detail(source={"Network":"Company value"},sources=[{}])["rows"][0]
        self.assertTrue(empty["empty"])
        self.assertTrue(all(f["value"].get("empty") for f in empty["fields"]))
        self.assertEqual(self.detail(sources=[])["rows"],[])

    def test_payment_only_address_line_break_normalization(self):
        raw="First floor,\r\n  Street,\nCity,\rState"
        company=dict(source_data=json.dumps({'Street':raw}),address=raw,network=None)
        contact=dict(id=1,source_data=json.dumps({'Street':raw}),address=raw,name=None,
                     contact_type=None,job_position=None,email=None,phone=None,landline_no=None,source_row=2)
        detail=build_company_detail(company,[contact],lambda key:True)
        payment=next(s for s in detail['sections'] if s['id']=='payment')
        address=next(f for f in payment['rows'][0]['fields'] if f['key']=='address')
        self.assertEqual(address['value']['text'],'First floor, Street, City, State')
        self.assertEqual(detail['contacts'][0]['by_key']['address']['value']['text'],raw)
        contact_section=next(s for s in detail['sections'] if s['id']=='contact')
        company_address=next(f for f in contact_section['fields'] if f['key']=='address')
        self.assertEqual(company_address['value']['text'],raw)
        self.assertEqual(contact['address'],raw)

    def test_template_escaping_and_empty_display(self):
        from jinja2 import Environment, ChoiceLoader, DictLoader, FileSystemLoader
        env=Environment(autoescape=True,loader=ChoiceLoader([
            DictLoader({"base.html":"{% block content %}{% endblock %}"}),
            FileSystemLoader(str(Path(__file__).resolve().parents[1]/"templates"))]))
        section=self.detail(source={"Payment Terms":"<script>alert(1)</script>"},addresses=["<b>Street</b>"])
        detail={"sections":[section],"header":{},"by_key":{},"contacts":[],"has_source":True}
        html=env.get_template("company.html").render(detail=detail,company={"id":1,"company_name":"Synthetic","country":"Test"},
                fields=[],user={"role":"USER"},status={"flags":[]},sales_labels={},back_url="/search")
        self.assertIn('class="xl-grid xl-payment"',html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',html)
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn('&lt;b&gt;Street&lt;/b&gt;',html)
        self.assertNotIn('<b>Street</b>',html)
        self.assertIn('Not provided',html)
        self.assertIn('href="#sec-payment"',html)
        self.assertEqual(html.count('class="xl-payment-row'),1)
        self.assertIn('data-copy-block',html)

if __name__=="__main__": unittest.main()
