"""UI asset consistency on a disposable app with synthetic data only."""
import importlib
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

SRC=Path(__file__).resolve().parent.parent
TMP=Path(tempfile.mkdtemp(prefix="directory-ui-navigation-"))
for name in ("app.py","utils","templates","static"):
    source=SRC/name
    if source.is_dir(): shutil.copytree(source,TMP/name,ignore=shutil.ignore_patterns("__pycache__"))
    else: shutil.copy2(source,TMP/name)
os.environ.update(SECRET_KEY="synthetic-ui-session-key",APP_ENV="development",COOKIE_SECURE="0",
                  MFA_ENABLED="0",MFA_ENROLLMENT_ENABLED="0",ADMIN_PASSWORD="Synthetic-UI-Password!")
os.chdir(TMP)
sys.path.insert(0,str(TMP))
A=importlib.import_module("app")


class UINavigationTests(unittest.TestCase):
    def test_theme_loads_before_styles_and_is_available_when_signed_out(self):
        html=A.app.test_client().get('/login').get_data(as_text=True)
        script=re.search(r'<script\b[^>]*src="[^"]*/js/theme\.js[^\"]*"[^>]*>',html)
        self.assertIsNotNone(script)
        self.assertLess(script.start(),html.index('<link rel="stylesheet"'))
        self.assertNotRegex(script.group(),r'\b(?:async|defer)\b')
        self.assertIn('aria-label="Appearance"',html)
        for value in ('light','dark','system'): self.assertIn(f'<option value="{value}">',html)

    def test_changed_assets_with_preserved_timestamp_get_new_urls(self):
        for filename in ("css/style.css","js/app.js","js/theme.js"):
            asset=TMP/"static"/filename
            original=asset.read_bytes(); stamp=asset.stat()
            with A.app.test_request_context():
                before=A.url_for("static",filename=filename)
                try:
                    asset.write_bytes(original+b"\n/* synthetic revision */\n")
                    os.utime(asset,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
                    self.assertNotEqual(before,A.url_for("static",filename=filename))
                finally:
                    asset.write_bytes(original)
                    os.utime(asset,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))

    def test_navigation_and_repeated_requests_share_current_assets(self):
        client=A.app.test_client()
        with A.db() as con:
            con.execute("INSERT INTO users(username,password_hash,role,status) VALUES(?,?,?,?)",
                        ("synthetic-ui-admin",A.generate_password_hash("Synthetic-UI-Password!"),"ADMIN","ACTIVE"))
        html=client.get("/login").get_data(as_text=True)
        token=re.search(r'name="csrf_token" value="([^"]+)"',html).group(1)
        response=client.post("/login",data={"username":"synthetic-ui-admin","password":"Synthetic-UI-Password!","csrf_token":token})
        self.assertEqual(response.status_code,302)
        expected=None
        # Returning to previous pages and refreshing must render the same shared shell/assets.
        for route in ("/admin","/admin/users","/admin/imports","/admin/records","/search","/admin","/admin"):
            response=client.get(route)
            self.assertEqual(response.status_code,200,route)
            self.assertEqual(response.headers.get("Cache-Control"),"no-store",route)
            html=response.get_data(as_text=True)
            assets=re.findall(r'(?:href|src)="(/static/[^\"]+)"',html)
            self.assertEqual(len(assets),3,route)
            if expected is None: expected=assets
            self.assertEqual(assets,expected,route)
            self.assertIn('class="topbar"',html)
            for asset in assets:
                with client.get(asset) as response: self.assertEqual(response.status_code,200)


if __name__=="__main__": unittest.main()
