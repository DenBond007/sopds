#!/usr/bin/env python3
"""Install only this user's SOPDS timer, after validating its concrete paths."""
import argparse
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--env-file', type=Path, required=True)
parser.add_argument('--enable', action='store_true')
args = parser.parse_args()
if not args.env_file.is_file():
    parser.error('Private container env file does not exist')
if any(c.isspace() or c in '%"' for c in str(root) + str(args.env_file.resolve())):
    parser.error('Use paths without whitespace, percent signs or quotes for this unit template')
units = Path.home()/'.config/systemd/user'
units.mkdir(parents=True,exist_ok=True)
service = (root/'infra/systemd/sopds-maintenance.service.example').read_text().replace('/path/to/sopds', str(root))
service = service.replace(str(root/'.local/container.env'), str(args.env_file.resolve()))
for name, text in [('sopds-maintenance.service',service),('sopds-maintenance.timer',(root/'infra/systemd/sopds-maintenance.timer.example').read_text())]:
    target = units/name
    if target.exists() and target.read_text() != text:
        parser.error('Existing different unit will not be overwritten: ' + name)
    target.write_text(text)
subprocess.run(['systemd-analyze','--user','verify',str(units/'sopds-maintenance.service'),str(units/'sopds-maintenance.timer')],check=True)
if args.enable:
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    subprocess.run(['systemctl','--user','enable','--now','sopds-maintenance.timer'],check=True)
print('SOPDS user timer installed' + (' and enabled' if args.enable else '; activation requires --enable'))
