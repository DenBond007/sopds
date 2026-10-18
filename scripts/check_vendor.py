"""Detect unreviewed changes in bundled compatibility components."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
manifest = root/'docs/modernization/vendor-manifest.json'
components = {
    'constance': ['constance/'],
    'legacy-zip': ['opds_catalog/zipf.py'],
    'frontend-vendor': ['static/js/vendor/', 'sopds_web_backend/static/js/vendor/'],
}
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--write', action='store_true', help='Accept reviewed component changes explicitly')
args=parser.parse_args()
paths=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode().split('\0')
actual={}
for name, prefixes in components.items():
    digest=hashlib.sha256()
    count=0
    for relative in sorted(p for p in paths if p and any(p==prefix or p.startswith(prefix) for prefix in prefixes)):
        path=root/relative
        if not path.is_file():
            continue
        digest.update(relative.encode()+b'\0'+hashlib.sha256(path.read_bytes()).digest())
        count+=1
    actual[name]={'sha256':digest.hexdigest(),'files':count}
if args.write:
    manifest.write_text(json.dumps(actual,indent=2)+'\n')
    print('Recorded reviewed vendor fingerprints')
else:
    if json.loads(manifest.read_text()) != actual:
        raise SystemExit('Bundled components changed: review and test before updating vendor-manifest.json')
    print('Bundled component fingerprints match')
