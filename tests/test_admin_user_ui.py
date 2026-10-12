"""Synthetic admin-form rendering and installed-browser interaction checks; no working app import."""
import json, os, re, runpy, subprocess, unittest
from pathlib import Path
from unittest.mock import patch
from html.parser import HTMLParser
state=runpy.run_path(str(Path(__file__).resolve().parent/"test_admin_verification.py"))
A=state["A"]; TMP=state["TMP"]
EDGE=Path(os.environ.get("ProgramFiles(x86)","C:/Program Files (x86)"))/"Microsoft/Edge/Application/msedge.exe"

HARNESS=r'''
<script>
window.addEventListener('load', () => {
  const failures = [], checks = [];
  const assert = (name, ok) => { checks.push(name); if (!ok) failures.push(name); };
  const input = (el, value, event='input') => { el.value=value; el.dispatchEvent(new Event(event, {bubbles:true})); };
  try {
    const account=document.querySelector('.user-form'), panel=account.querySelector('[data-admin-verification]');
    const role=account.elements.role, status=account.elements.status, password=account.elements.password;
    assert('ordinary edits start without verification',panel.hidden && panel.disabled && account.checkValidity());
    input(account.elements.first_name,'Synthetic name');
    account.querySelector('[name="sales_fields"]').click();
    account.querySelector('[name="countries"]').click();
    assert('name and permissions do not activate verification',panel.hidden && !new FormData(account).has('current_password'));
    input(role,'ADMIN','change');
    assert('role change requires credentials',!panel.hidden && !panel.disabled && panel.querySelector('[name="current_password"]').required && !account.checkValidity());
    input(panel.querySelector('[name="current_password"]'),'synthetic-only');
    const code=panel.querySelector('[name="code"]'); if(code) input(code,'123456');
    input(role,account.dataset.initialRole,'change');
    assert('role revert hides and clears credentials',panel.hidden && panel.querySelector('[name="current_password"]').value==='' && (!code || code.value===''));
    input(status,'DISABLED','change'); assert('status change activates verification',!panel.hidden);
    input(status,account.dataset.initialStatus,'change'); assert('status revert hides verification',panel.hidden);
    input(password,'Synthetic-Reset-Password!'); assert('password reset activates verification',!panel.hidden);
    input(password,''); assert('clearing reset password hides verification',panel.hidden);
    const policy=document.querySelector('[data-verification="policy"]');
    if(policy) {
      const save=policy.querySelector('button[type="submit"]'), verify=policy.querySelector('[data-admin-verification]');
      assert('unchanged policy disabled',save.disabled && verify.hidden);
      input(policy.elements.required,policy.dataset.initialRequired==='0'?'1':'0','change');
      assert('changed policy enabled and verified',!save.disabled && !verify.hidden && !policy.checkValidity());
      input(verify.querySelector('[name="current_password"]'),'synthetic-policy-only');
      assert('credentials independent across forms',panel.querySelector('[name="current_password"]').value==='');
      input(policy.elements.required,policy.dataset.initialRequired,'change');
      assert('policy revert clears credentials',save.disabled && verify.hidden && verify.querySelector('[name="current_password"]').value==='');
      assert('policy select uses existing form styling',getComputedStyle(policy.elements.required).height==='40px');
    }
    const open=document.querySelector('[data-delete-open]'), deletion=document.querySelector('[data-verification="delete"]'), cancel=deletion.querySelector('[data-delete-cancel]');
    assert('deletion collapsed initially',!open.hidden && deletion.hidden && open.getAttribute('aria-expanded')==='false');
    open.focus(); assert('delete trigger focusable',document.activeElement===open);
    open.click(); assert('expanded deletion fits viewport',document.documentElement.scrollWidth<=window.innerWidth);
    assert('deletion expands and focuses password',!deletion.hidden && open.getAttribute('aria-expanded')==='true' && document.activeElement===deletion.querySelector('[name="current_password"]'));
    input(deletion.querySelector('[name="current_password"]'),'synthetic-delete-only');
    const deleteCode=deletion.querySelector('[name="code"]'); if(deleteCode) input(deleteCode,'123456');
    let confirmations=0; window.confirm=()=>{ confirmations++; return false; };
    const submission=new Event('submit',{bubbles:true,cancelable:true}); deletion.dispatchEvent(submission);
    assert('final confirmation retained',confirmations===1 && submission.defaultPrevented);
    cancel.click(); assert('cancel clears and restores focus',deletion.hidden && deletion.querySelector('[name="current_password"]').value==='' && (!deleteCode || deleteCode.value==='') && document.activeElement===open);
    assert('MFA-off requests password only',document.querySelectorAll('[data-admin-verification] [name="code"]').length===0 || !!code);
    assert('recovery stays expandable',!policy || document.querySelector('.security-recovery summary'));
    assert('no horizontal overflow',document.documentElement.scrollWidth<=window.innerWidth);
    assert('verification controls have labels',[...document.querySelectorAll('[data-admin-verification] input,[data-admin-verification] select')].every(el=>el.labels.length>0));
  } catch(error) { failures.push(String(error)); }
  const result=document.createElement('pre'); result.id='ui-check-results'; result.textContent=JSON.stringify({checks:checks.length,failures,width:window.innerWidth}); document.body.append(result);
});
</script>
'''

class AdminUserUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=state["AdminVerificationTests"](); cls.fixture.setUp()
        with A.db() as c:
            country=c.execute("INSERT INTO countries(name) VALUES('Synthetic country with a longer descriptive name')").lastrowid
            c.execute("INSERT INTO companies(country_id,company_name) VALUES(?,'Synthetic company')",(country,))
        cls.pages={}
        for enabled in (True,False):
            with patch.dict(A.app.config,MFA_ENABLED=enabled,MFA_ENROLLMENT_ENABLED=enabled):
                response=cls.fixture.client.get(cls.fixture.base); assert response.status_code==200
                html=response.get_data(as_text=True)
            # Relative code/assets only, all in this test's disposable copy.
            html=html.replace('/static/','static/')
            cls.pages[enabled]=html
            (TMP/('mfa-on.html' if enabled else 'mfa-off.html')).write_text(html.replace('</body>',HARNESS+'</body>'),encoding='utf-8')
        fallback=re.sub(r'<script\b[^>]*>.*?</script>','',cls.pages[True],flags=re.S)
        (TMP/'no-js.html').write_text(fallback,encoding='utf-8')
    def test_no_javascript_fallback_and_creation_requirements(self):
        class Forms(HTMLParser):
            def __init__(self):super().__init__(); self.form=None; self.forms={}; self.depth=0
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if tag=='form':self.assert_no_nested();self.form=attrs.get('data-verification','other');self.forms[self.form]=[];self.depth+=1
                if tag=='fieldset' and 'data-admin-verification' in attrs: self.forms[self.form].append(attrs)
            def assert_no_nested(self):assert self.depth==0
            def handle_endtag(self,tag):
                if tag=='form':self.depth-=1;self.form=None
        parser=Forms(); parser.feed(self.pages[True])
        for name in ('account','policy','delete'):
            self.assertEqual(len(parser.forms[name]),1);self.assertNotIn('hidden',parser.forms[name][0]);self.assertNotIn('disabled',parser.forms[name][0])
        self.assertNotIn('name="code"',self.pages[False]); self.assertNotIn('data-verification="policy"',self.pages[False])
        html=self.fixture.client.get('/admin/users/create').get_data(as_text=True)
        self.assertIn('data-verification-required required',html);self.assertNotIn('data-verification="account"',html)
    @unittest.skipUnless(EDGE.exists(),'Installed Edge unavailable; no browser installed for this test')
    def test_browser_interactions_desktop_narrow_and_mfa_off(self):
        for name,size in (('mfa-on','1440,2200'),('mfa-on','520,2400'),('mfa-off','1440,2200')):
            profile=TMP/('edge-'+name+'-'+size.replace(',','-')); image=TMP/(name+'-'+size.replace(',','-')+'.png')
            command=[str(EDGE),'--headless','--disable-gpu','--no-first-run','--user-data-dir='+str(profile),'--window-size='+size,'--virtual-time-budget=1500','--screenshot='+str(image),'--dump-dom',(TMP/(name+'.html')).as_uri()]
            result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
            match=re.search(r'<pre id="ui-check-results">(.*?)</pre>',result.stdout,re.S)
            self.assertIsNotNone(match,result.stderr[-1000:]); data=json.loads(match.group(1));self.assertEqual(data['failures'],[],data)
            print(name,size,data['checks'],'browser checks passed; viewport',data['width'],flush=True)
        print('Synthetic browser artifacts:',TMP,flush=True)

if __name__=='__main__':unittest.main()
