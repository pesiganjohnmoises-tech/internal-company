"""Readable text/status colors in both shared palettes, without application startup."""
import re
import unittest
from pathlib import Path

CSS=(Path(__file__).resolve().parents[1]/'static/css/style.css').read_text(encoding='utf-8')

def palette(selector):
    body=re.search(re.escape(selector)+r'\s*\{([^}]+)',CSS).group(1)
    return dict(re.findall(r'(--[\w-]+):\s*(#[a-fA-F0-9]+)\s*;',body))

def luminance(color):
    color=color.lstrip('#')
    if len(color)==3: color=''.join(c*2 for c in color)
    channels=[int(color[i:i+2],16)/255 for i in (0,2,4)]
    return sum((v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4)*weight
               for v,weight in zip(channels,(.2126,.7152,.0722)))

class ThemeColorTests(unittest.TestCase):
    def test_normal_text_and_status_contrast(self):
        light=palette(':root')
        for mode,colors in (('light',light),('dark',{**light,**palette(':root[data-theme="dark"]')})):
            pairs=[('--ink','--surface'),('--ink-2','--surface'),('--muted','--bg'),
                   ('--faint','--surface'),('--brand','--surface'),('--on-brand','--brand')]
            pairs += [(f'--{tone}',f'--{tone}-bg') for tone in ('good','warn','bad','info','neutral')]
            for foreground,background in pairs:
                with self.subTest(mode=mode,foreground=foreground,background=background):
                    lo,hi=sorted((luminance(colors[foreground]),luminance(colors[background])))
                    self.assertGreaterEqual((hi+.05)/(lo+.05),4.5)

if __name__=='__main__': unittest.main()
