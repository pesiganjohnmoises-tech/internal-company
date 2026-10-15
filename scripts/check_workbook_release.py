"""Run only synthetic/code-only release suites in separate processes; never import the app."""
import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
SUITES=(
    'test_workbook_access.py','test_workbook_submissions.py','test_workbook_routes.py',
    'test_workbook_review.py','test_workbook_approval.py','test_workbook_release.py',
    'test_import_safety.py','test_import_history.py','test_scheduled_backup.py','test_upload_archive.py',
    'test_security_controls.py','test_admin_verification.py','test_account_restrictions.py',
    'test_admin_mfa_reset.py','test_mfa_storage.py','test_mfa_login.py','test_session_contention.py',
    'test_ui_navigation.py','test_theme_colors.py',
)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,help='New JSON report path; refuses to overwrite an existing file.')
    parser.add_argument('--temp-root',type=Path,help='Existing private rehearsal directory outside this checkout, for testing the hosted filesystem.')
    args=parser.parse_args()
    if args.report and args.report.exists(): parser.error('Report already exists; choose a new path.')
    env=dict(os.environ)
    if args.temp_root:
        folder=args.temp_root.resolve()
        if not folder.is_dir() or folder==ROOT or folder.is_relative_to(ROOT):
            parser.error('Temporary root must be an existing directory outside this checkout.')
        env.update(TMPDIR=str(folder),TEMP=str(folder),TMP=str(folder))
    results=[]
    for name in SUITES:
        started=time.monotonic()
        result=subprocess.run([sys.executable,'-B','-W','ignore::ResourceWarning',str(ROOT/'tests'/name)],
                              cwd=ROOT,env=env,capture_output=True,text=True)
        output=result.stdout+'\n'+result.stderr
        unit=re.search(r'Ran (\d+) tests? in',output)
        checks=re.findall(r'(\d+) (?:total )?checks passed',output)
        count=int(unit[1]) if unit else int(checks[-1]) if checks else None
        results.append({'suite':name,'passed':result.returncode==0,'checks':count,
                        'seconds':round(time.monotonic()-started,2)})
        print(('PASS ' if result.returncode==0 else 'FAIL ')+name+' checks='+str(count),flush=True)
        if result.returncode: print(output[-6000:],flush=True)
    node=shutil.which('node')
    if node:
        result=subprocess.run([node,str(ROOT/'tests/test_theme.cjs')],cwd=ROOT,env=env,capture_output=True,text=True)
        results.append({'suite':'test_theme.cjs','passed':result.returncode==0,'checks':None})
        print(('PASS ' if result.returncode==0 else 'FAIL ')+'test_theme.cjs',flush=True)
        if result.returncode: print((result.stdout+result.stderr)[-6000:],flush=True)
    else:
        results.append({'suite':'test_theme.cjs','passed':False,'checks':None,'limitation':'Node is unavailable; run this code-only suite separately.'})
        print('NOT RUN test_theme.cjs: Node unavailable',flush=True)
    report={'created_utc':datetime.now(timezone.utc).isoformat(),'python':sys.version.split()[0],
            'platform':sys.platform,'packages':{name:version(name) for name in ('Flask','Flask-WTF','openpyxl','pyotp','qrcode','cryptography')},
            'suites':results,'passed':all(row['passed'] for row in results),
            'counted_checks':sum(row['checks'] or 0 for row in results),
            'scope':'Synthetic/code-only local checks. No production, deployment endpoint, company fixtures, or hosted HTTP configuration tested. ResourceWarnings from legacy connection lifetimes are suppressed.'}
    if args.report:
        with args.report.open('x',encoding='utf-8') as out: json.dump(report,out,indent=2); out.write('\n')
    print('Counted checks: '+str(report['counted_checks'])+'; all suites passed: '+str(report['passed']),flush=True)
    return 0 if report['passed'] else 1

if __name__=='__main__': raise SystemExit(main())
