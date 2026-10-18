#!/usr/bin/env python3
"""Install one scoped user cron block; preserve all existing jobs verbatim."""
import argparse
from pathlib import Path
import shlex
import subprocess

root=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--env-file',type=Path,required=True)
parser.add_argument('--backup-dir',type=Path,required=True)
parser.add_argument('--amd64',action='store_true')
args=parser.parse_args()
if not args.env_file.is_file():
    parser.error('Private container env does not exist')
paths=[str(root),str(args.env_file.resolve()),str(args.backup_dir.resolve())]
if any('%' in p or '\n' in p or '\r' in p for p in paths):
    parser.error('Unsupported cron path')
begin='# BEGIN SOPDS VERIFIED BACKUP'
end='# END SOPDS VERIFIED BACKUP'
existing=subprocess.run(['crontab','-l'],capture_output=True,text=True)
if existing.returncode not in (0,1):
    raise SystemExit('Cannot read user crontab')
text=existing.stdout
if text.count(begin)!=text.count(end) or text.count(begin)>1:
    raise SystemExit('Malformed existing SOPDS cron block; refusing to overwrite')
if begin in text:
    start=text.index(begin); stop=text.index(end,start)+len(end)
    text=text[:start]+text[stop:].lstrip('\n')
command=['/usr/bin/python3',str(root/'scripts/ops/daily-maintenance.py'),
         '--env-file',str(args.env_file.resolve()),'--backup-dir',str(args.backup_dir.resolve())]
if args.amd64:
    command.append('--amd64')
line='*/15 * * * * umask 077 && '+shlex.join(command)+' >> '+shlex.quote(str(root/'.local/logs/maintenance-schedule.log'))+' 2>&1'
block=begin+'\n'+line+'\n'+end+'\n'
result=subprocess.run(['crontab','-'],input=text.rstrip('\n')+'\n'+block,capture_output=True,text=True)
if result.returncode:
    raise SystemExit('Could not install SOPDS user schedule')
print('Installed user cron: daily verified backup after 03:30 Europe/Moscow; existing jobs preserved')
