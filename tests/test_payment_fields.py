"""Focused Payment checks using synthetic SQLite rows; no application startup."""
import json, sqlite3, sys, unittest
from contextlib import closing
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from utils.fields import build_company_detail

class PaymentFieldsTest(unittest.TestCase):
    def detail(self,city=" Mumbai ",state=" Maharashtra ",source=None,denied=(),sources=None):
        with closing(sqlite3.connect(":memory:")) as con:
            con.row_factory=sqlite3.Row
            row=con.execute("SELECT ? source_data, ? city, ? state, ? address, ? network, 'Synthetic agent' company_name, 'Test country' country",
                            (json.dumps(source or {}),city,state,"Stored street","Stored network")).fetchone()
            data=dict(source or {}); data.update(City=city,State=state)
            contacts=[dict(id=i+1,source_data=json.dumps(s),name="Test contact",contact_type=None,
                           job_position=None,email=None,phone=None,landline_no=None,address=None,source_row=i+2)
                      for i,s in enumerate(sources if sources is not None else [data])]
            detail=build_company_detail(row,contacts,lambda k:k not in denied)
            self.assertEqual(len(detail["contacts"]),len(contacts))
            return next(s for s in detail["sections"] if s["id"]=="payment")

    def test_order_and_stored_values(self):
        section=self.detail(source={"Payment Terms":"Credit","Network":"Stale source network",
                                    "Network Expiry":"Apr 08, 2027","Street":"Stale source street",
                                    "KYC Status":"Pending","Mapping Status":"Done"})
        fields=section["rows"][0]["fields"]
        self.assertEqual([f["label"] for f in fields],["Payment Terms","Network","Network Expiry","City and state","KYC Status","Mapping Status"])
        self.assertEqual([f["value"]["text"] for f in fields],
                         ["Credit","Stale source network","8 Apr 2027","Mumbai, Maharashtra","Pending","Done"])

    def test_missing_values_and_partial_locations(self):
        fields={f["key"]:f for f in self.detail(city="nan",state=None)["rows"][0]["fields"]}
        for key in ("x_paymentterms","x_networkexpiry","city_state","x_kycstatus","x_mappingstatus"):
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

    def test_distinct_rows_and_no_company_fallback(self):
        section=self.detail(sources=[{"Association":f"Network {i}","Town":f"City {i}","Province":"State",
                                      "Payment Terms":str(i)} for i in range(12)])
        self.assertEqual(len(section["rows"]),12)
        self.assertEqual(len({r["id"] for r in section["rows"]}),12)
        for i,row in enumerate(section["rows"]):
            fields={f["key"]:f for f in row["fields"]}
            self.assertEqual(fields["network"]["value"]["text"],f"Network {i}")
            self.assertEqual(fields["city_state"]["value"]["text"],f"City {i}, State")
        empty=self.detail(source={"Network":"Company value"},sources=[{}])["rows"][0]
        self.assertTrue(empty["empty"])
        self.assertTrue(all(f["value"].get("empty") for f in empty["fields"]))
        self.assertEqual(self.detail(sources=[])["rows"],[])

    def test_template_escaping_and_empty_display(self):
        from jinja2 import Environment, ChoiceLoader, DictLoader, FileSystemLoader
        env=Environment(autoescape=True,loader=ChoiceLoader([
            DictLoader({"base.html":"{% block content %}{% endblock %}"}),
            FileSystemLoader(str(Path(__file__).resolve().parents[1]/"templates"))]))
        section=self.detail(source={"Payment Terms":"<script>alert(1)</script>"})
        detail={"sections":[section],"header":{},"by_key":{},"contacts":[],"has_source":True}
        html=env.get_template("company.html").render(detail=detail,company={"id":1,"company_name":"Synthetic","country":"Test"},
                fields=[],user={"role":"USER"},status={"flags":[]},sales_labels={},back_url="/search")
        self.assertIn('class="xl-grid xl-payment"',html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',html)
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn('Not provided',html)
        self.assertIn('href="#sec-payment"',html)
        self.assertEqual(html.count('class="xl-payment-row'),1)
        self.assertIn('data-copy-block',html)

if __name__=="__main__": unittest.main()
