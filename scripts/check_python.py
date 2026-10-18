"""Compile tracked Python sources without importing them or writing bytecode."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
paths = subprocess.check_output(['git','-C',str(root),'ls-files','-z','--','*.py']).decode().split('\0')
for name in filter(None, paths):
    source = root/name
    if source.is_file():
        compile(source.read_bytes(), name, 'exec')
print('Tracked Python syntax is valid')
