#!/usr/bin/env python3
"""Test the standalone YAML using new local containers and synthetic data only.

Developer/CI tool, never required on a NAS. Cleans up only its own Compose projects.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DB_IMAGE = 'postgres:17.11-bookworm@sha256:91eb910c44c7ed13f7f1a4ccadaa9ca72ef14cddc04cacb6e070e48eb44731a3'
RELEASE = 'sha256:' + 'a' * 64
PASSWORD = 'Synthetic-admin-only-8!Bx32'


def run(command, *, check=True, **kwargs):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs)
    if check and result.returncode:
        raise RuntimeError('Command failed: ' + ' '.join(command[:5]) + '\n' + result.stdout[-3000:] + result.stderr[-3000:])
    return result


class Stack:
    def __init__(self, args, directory, suffix):
        self.args = args
        self.root = directory / suffix
        self.root.mkdir(mode=0o777)
        self.root.chmod(0o777)
        self.name = 'sopds-container-m11-' + secrets.token_hex(4)
        self.file = self.root / 'compose.json'
        self.command = ['docker', 'compose', '-p', self.name, '-f', str(self.file)]
        (self.root / 'books').mkdir(mode=0o755)
        # All mounts are under this fresh synthetic tree. Numeric owners create
        # their own directories; no host root or recursive chown is needed.
        for image, uid, names in ((args.app_image, '10001:10001', 'static caddy-data caddy-config'),
                                   (DB_IMAGE, '999:999', 'pgdata')):
            run(['docker', 'run', '--rm', '--user', uid, '--entrypoint', '/bin/sh',
                 '--mount', 'type=bind,src=' + str(self.root) + ',dst=/probe', image,
                 '-ec', 'for name in ' + names + '; do mkdir /probe/$name; chmod 0700 /probe/$name; done'])
        text = (ROOT / 'infra/truenas/compose.template.yaml').read_text()
        release = args.app_image.rsplit('@', 1)[1] if '@sha256:' in args.app_image else RELEASE
        replacements = {
            '__APP_IMAGE__': 'ghcr.io/denbond007/sopds-nas-app@' + release,
            '__CADDY_IMAGE__': 'ghcr.io/denbond007/sopds-nas-caddy@' + release,
            '__APP_RELEASE__': release,
            '/mnt/CHANGE_ME/books': str(self.root / 'books'),
            '/mnt/CHANGE_ME/sopds': str(self.root),
            'CHANGE_ME_DJANGO_64_RANDOM_HEX': secrets.token_hex(32),
            'CHANGE_ME_POSTGRES_64_RANDOM_HEX': secrets.token_hex(32),
            'CHANGE_ME_APP_DB_64_RANDOM_HEX': secrets.token_hex(32),
            'CHANGE_ME_ADMIN_USERNAME': 'nas_admin',
            'CHANGE_ME@example.invalid': 'admin@example.invalid',
            'CHANGE_ME_ADMIN_PASSWORD_16_OR_MORE_CHARACTERS': PASSWORD,
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        self.config = json.loads(run(['docker', 'compose', '-f', '-', 'config', '--format', 'json'], input=text).stdout)
        self.config.pop('name', None)
        for network in self.config.get('networks', {}).values():
            network.pop('name', None)
        for name, service in self.config['services'].items():
            service['image'] = args.caddy_image if name == 'caddy' else DB_IMAGE if name == 'db' else args.app_image
            service['pull_policy'] = 'never'
        # A host-side test runner needs a non-internal test network for the
        # loopback publication. Production YAML keeps db internal and unpublished.
        self.config['services']['db']['networks'] = ['backend', 'ingress']
        self.config['services']['db']['ports'] = [{'target': 5432, 'published': '0', 'host_ip': '127.0.0.1'}]
        port = self.config['services']['caddy']['ports'][0]
        port['published'] = '0'
        port['host_ip'] = '127.0.0.1'
        self.write()

    def write(self):
        self.file.write_text(json.dumps(self.config))
        self.file.chmod(0o600)

    def compose(self, *args, **kwargs):
        return run(self.command + list(args), **kwargs)

    def up(self, succeeds=True):
        self.write()
        result = self.compose('up', '-d', '--wait', '--wait-timeout', '150', check=False, timeout=200)
        (self.args.logs / (self.name + '-up.log')).write_text(result.stdout + result.stderr)
        assert (result.returncode == 0) == succeeds, result.stdout[-2500:] + result.stderr[-2500:]
        if succeeds:
            self.compose('exec', '-T', 'web', 'python', 'manage.py', 'sopds_initialize', '--check-ready')
        else:
            for name in ('web', 'scanner', 'annotations', 'caddy'):
                assert not self.compose('ps', '-q', '--status', 'running', name).stdout.strip(), name + ' started after init failure'

    def python(self, source):
        return self.compose('run', '--rm', '--no-deps', '-T', '--entrypoint', 'python', 'web', '-c', source).stdout.strip()

    def stop_runtime(self):
        self.compose('stop', 'web', 'scanner', 'annotations', 'caddy', timeout=360)

    def published_port(self, service, target):
        identifier = self.compose('ps', '-q', service).stdout.strip()
        data = json.loads(run(['docker', 'inspect', '--format', '{{json .NetworkSettings.Ports}}', identifier]).stdout)
        port = data[str(target) + '/tcp'][0]['HostPort']
        assert 0 < int(port) < 65536, 'Docker did not assign a real port'
        return port

    def health(self):
        certificate = self.compose('exec', '-T', 'caddy', 'cat', '/data/caddy/pki/authorities/local/root.crt').stdout
        ca = self.root / 'root.crt'
        ca.write_text(certificate)
        port = self.published_port('caddy', 8443)
        base = ['curl', '-q', '--noproxy', '*', '--silent', '--show-error', '--max-time', '15',
                '--cacert', str(ca), '--resolve', '192.0.2.10:' + port + ':127.0.0.1',
                '--output', '/dev/null', '--write-out', '%{http_code}']
        url = 'https://192.0.2.10:' + port + '/opds/'
        assert run(base + [url]).stdout == '401'
        assert run(base + ['--user', 'nas_admin:' + PASSWORD, url]).stdout == '200'
        assert self.compose('exec', '-T', 'web', 'id', '-u').stdout.strip() == '10001'
        assert self.compose('exec', '-T', 'db', 'id', '-u').stdout.strip() == '999'
        assert self.compose('exec', '-T', 'caddy', 'id', '-u').stdout.strip() == '10001'
        assert self.compose('exec', '-T', 'web', 'sh', '-c', 'touch /library/forbidden', check=False).returncode != 0
        assert self.compose('exec', '-T', 'caddy', 'test', '-s', '/srv/static/admin/css/base.css').returncode == 0
        return certificate

    def close(self):
        try:
            logs = self.compose('logs', '--no-color', check=False).stdout
            (self.args.logs / (self.name + '.log')).write_text(logs)
            self.compose('down', '--volumes', '--remove-orphans', timeout=360)
        finally:
            # Remove only this verified disposable tree, as its numeric owners.
            for image, uid, names in ((self.args.app_image, '10001:10001', 'static caddy-data caddy-config'),
                                       (DB_IMAGE, '999:999', 'pgdata')):
                run(['docker', 'run', '--rm', '--user', uid, '--entrypoint', '/bin/sh',
                     '--mount', 'type=bind,src=' + str(self.root) + ',dst=/probe', image,
                     '-ec', 'for name in ' + names + '; do chmod -R u+rwX /probe/$name; rm -rf /probe/$name; done'])


@contextmanager
def stack(args, directory, suffix):
    probe = Stack(args, directory, suffix)
    try:
        yield probe
    finally:
        probe.close()


SETUP = '''
import django; django.setup()
from constance.backends.database.models import Constance
from django.contrib.auth import get_user_model
from opds_catalog.models import Genre, Catalog, Book, bookshelf, ReadingState
user=get_user_model().objects.get(username='nas_admin')
user.email='keep@example.invalid'; user.save()
Genre.objects.all().delete()
Genre.objects.create(genre='custom', section='Keep', subsection='Keep')
Constance.objects.update_or_create(key='SOPDS_MAXITEMS', defaults={'value': 37})
catalog=Catalog.objects.create(cat_name='.',path='.')
book=Book.objects.create(catalog=catalog,filename='probe.fb2',path='.',format='fb2',title='Keep',search_title='KEEP',annotation='Keep',avail=2)
bookshelf.objects.create(user=user,book=book)
ReadingState.objects.create(user=user,book=book,position=4321)
'''
SNAPSHOT = '''
import django; django.setup()
import json
from django.contrib.auth import get_user_model
from constance.backends.database.models import Constance
from opds_catalog.models import Genre, Book, bookshelf, ReadingState
print(json.dumps([list(get_user_model().objects.values('id','username','email','password')), list(Genre.objects.values()), list(Constance.objects.filter(key='SOPDS_MAXITEMS').values()), list(Book.objects.values('id','title','annotation')), list(bookshelf.objects.values('id','user_id','book_id')), list(ReadingState.objects.values('user_id','book_id','position'))],sort_keys=True))
'''
INTERRUPT = '''
import django; django.setup()
import os, signal
from django.db import transaction
from opds_catalog import deployment
from opds_catalog.models import Genre
def interrupted_seed():
    with transaction.atomic():
        Genre.objects.create(genre='uncommitted',section='Probe',subsection='Probe')
        os.kill(os.getpid(), signal.SIGKILL)
deployment.seed_new_library=interrupted_seed
deployment.run_initialization()
'''


def full_suite(probe):
    port = probe.published_port('db', 5432)
    env = dict(os.environ, DJANGO_SETTINGS_MODULE='sopds.settings', DB_HOST='127.0.0.1', DB_PORT=port,
               DB_NAME='sopds', DB_USER='postgres', DB_PASS=probe.config['services']['db']['environment']['POSTGRES_PASSWORD'],
               SOPDS_SECRET_KEY=secrets.token_hex(32), SOPDS_ALLOWED_HOSTS='testserver,127.0.0.1,localhost',
               TIME_ZONE='UTC', SOPDS_BROWSER_TESTS='1')
    if Path('/opt/google/chrome/chrome').is_file():
        env['SOPDS_BROWSER_EXECUTABLE'] = '/opt/google/chrome/chrome'
    python = str(ROOT / '.venv/bin/python')
    for name, args in [('source', ['scripts/check_python.py']), ('vendor', ['scripts/check_vendor.py']),
                       ('check', ['manage.py', 'check']), ('schema', ['manage.py', 'makemigrations', '--check', '--dry-run']),
                       ('tests-full', ['manage.py', 'test', '--noinput'])]:
        result = run([python, *args], check=False, env=env, cwd=ROOT, timeout=360)
        (probe.args.logs / (name + '.log')).write_text(result.stdout + result.stderr)
        assert result.returncode == 0, name + ': ' + result.stdout[-4000:] + result.stderr[-4000:]
        print(name + ': OK', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-image', required=True)
    parser.add_argument('--caddy-image', required=True)
    parser.add_argument('--logs', type=Path, required=True)
    parser.add_argument('--full-suite', action='store_true')
    args = parser.parse_args()
    args.logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='sopds-m11-') as temporary:
        directory = Path(temporary)
        directory.chmod(0o755)
        with stack(args, directory, 'first') as probe:
            init = probe.config['services']['init']
            init['environment']['SOPDS_ADMIN_PASSWORD'] = 'CHANGE_ME_BAD_PASSWORD'
            probe.up(succeeds=False)
            init['environment']['SOPDS_ADMIN_PASSWORD'] = PASSWORD
            probe.up()
            ca = probe.health()
            print('First install, dependency failure, TLS/auth/RO/non-root: OK', flush=True)
            probe.stop_runtime()
            probe.python(SETUP)
            before = probe.python(SNAPSHOT)
            # This password and username must be ignored for existing installations.
            init['environment']['SOPDS_ADMIN_PASSWORD'] = 'invalid'
            init['environment']['SOPDS_ADMIN_USERNAME'] = 'do_not_create'
            probe.up()
            assert probe.python(SNAPSHOT) == before
            assert probe.health() == ca
            print('Repeat preserves accounts/settings/genres/books/shelves/positions/CA: OK', flush=True)
            probe.stop_runtime()
            # Wrong static permissions must invalidate readiness and block startup.
            probe.compose('run', '--rm', '--no-deps', '-T', '--entrypoint', 'chmod', 'init', '0500', '/srv/static')
            probe.up(succeeds=False)
            assert probe.compose('run', '--rm', '--no-deps', '-T', 'web', 'true', check=False).returncode != 0
            probe.compose('run', '--rm', '--no-deps', '-T', '--entrypoint', 'chmod', 'init', '0700', '/srv/static')
            probe.up()
            assert probe.python(SNAPSHOT) == before
            print('Mount failure blocks runtime; retry preserves data: OK', flush=True)
            probe.stop_runtime()
            original = init['environment']['DB_PASS']
            init['environment']['DB_PASS'] = secrets.token_hex(32)
            probe.up(succeeds=False)
            init['environment']['DB_PASS'] = original
            probe.up()
            assert probe.python(SNAPSHOT) == before
            print('Wrong existing DB password is refused without password reset: OK', flush=True)
            if args.full_suite:
                full_suite(probe)
        with stack(args, directory, 'interrupted') as probe:
            probe.config['services']['init']['command'] = ['python', '-c', INTERRUPT]
            probe.up(succeeds=False)
            probe.config['services']['init']['command'] = ['python', 'manage.py', 'sopds_initialize']
            probe.up()
            result = probe.python("import django; django.setup(); from opds_catalog.models import Genre; assert not Genre.objects.filter(genre='uncommitted').exists(); assert Genre.objects.count()>100; print('OK')")
            assert result == 'OK'
            probe.health()
            print('SIGKILL during atomic seed rolls back and resumes automatically: OK', flush=True)
    print('Synthetic smoke complete; only its own containers/networks/data removed.', flush=True)


if __name__ == '__main__':
    main()
