import importlib.util
import hashlib
import io
import tarfile
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load('truenas_prepare', 'scripts/truenas/prepare.py')
control = load('truenas_control', 'scripts/truenas/control.py')
builder = load('truenas_builder', 'scripts/truenas/build-kit.py')
maintenance = load('truenas_maintenance', 'scripts/ops/maintenance.py')
registry = load('truenas_registry', 'scripts/truenas/check-registry.py')


class TrueNASPreparationTests(TestCase):
    def setUp(self):
        self.settings = json.loads((ROOT / 'infra/truenas/settings.example.json').read_text())
        self.credentials = {key: character * 64 for key, character in zip(prepare.SECRET_KEYS, 'abc')}
        self.credentials['installation_id'] = 'f8a01901-73ee-4dab-8c4a-aef3619daff0'
        self.release = {'images': {key: {'tag': 'sopds-' + key + ':test'} for key in ('app', 'caddy', 'db')}}
        self.base = {'name': 'sopds-container', 'networks': {'backend': {'name': 'sopds-container_backend', 'internal': True},
                     'ingress': {'name': 'sopds-container_ingress'}}, 'volumes': {'static': {}}, 'services': {}}
        for name in ('web', 'scanner', 'annotations', 'manage', 'db', 'caddy'):
            self.base['services'][name] = {'image': 'old', 'build': {'context': '.'},
                'environment': {'DB_PASS': 'old', 'SOPDS_PRIVATE_HOST': 'old'},
                'volumes': [{'type': 'volume', 'source': 'static', 'target': '/srv/static', 'read_only': True}]}
        self.base['services']['manage']['profiles'] = ['tools']

    def test_render_detaches_build_and_project_names_and_preserves_read_only(self):
        app, bootstrap = prepare.render(self.base, self.settings, self.credentials, self.release)
        self.assertNotIn('name', app)
        self.assertNotIn('volumes', app)
        self.assertEqual(set(bootstrap['services']), {'db', 'manage'})
        self.assertEqual(bootstrap['services']['manage']['profiles'], ['tools'])
        self.assertEqual(set(bootstrap['networks']), {'backend'})
        self.assertNotIn('name', app['networks']['backend'])
        for service in app['services'].values():
            self.assertNotIn('build', service)
            self.assertEqual(service['pull_policy'], 'never')
            self.assertEqual(service['environment']['DB_PASS'], self.credentials['SOPDS_DB_PASSWORD'])
            self.assertEqual(service['labels'][prepare.LABEL], self.credentials['installation_id'])
            self.assertTrue(service['volumes'][0]['read_only'])
            self.assertFalse(service['volumes'][0]['bind']['create_host_path'])
            self.assertEqual(service['volumes'][0]['source'], self.settings['data_dir'] + '/static')
        self.assertEqual(app['services']['caddy']['ports'][0]['host_ip'], self.settings['host'])
        self.assertIn('build', self.base['services']['web'])

    def test_unsafe_or_overlapping_paths_and_ports_fail_before_secret_generation(self):
        for key, value in (('library', '/mnt/tank/apps'), ('backup_dir', '/mnt/tank/books/copies'),
                           ('package_dir', '/mnt/tank/../package'), ('library', '/mnt/tank/$HOME'),
                           ('library', '/mnt/tank'), ('port', 443), ('host', '127.0.0.1')):
            with self.subTest(key=key, value=value):
                settings = dict(self.settings, **{key: value})
                with self.assertRaises(ValueError):
                    prepare.validate_settings(settings)

    def test_prepare_is_private_and_reuses_secrets_across_host_change(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'compose.base.json').write_text(json.dumps(self.base))
            (root / 'release.json').write_text(json.dumps(self.release))
            old_umask = os.umask(0o077)
            try:
                deployment = prepare.prepare(root, self.settings)
                before = (deployment / 'secrets.json').read_bytes()
                prepare.prepare(root, dict(self.settings, host='192.0.2.11'))
            finally:
                os.umask(old_umask)
            self.assertEqual(before, (deployment / 'secrets.json').read_bytes())
            for path in deployment.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(deployment.stat().st_mode & 0o777, 0o700)
            app = json.loads((deployment / 'app.yaml').read_text())
            self.assertEqual(app['services']['web']['environment']['SOPDS_PRIVATE_HOST'], '192.0.2.11')
            self.assertNotIn('SOPDS_DB_PASSWORD', (root / 'release.json').read_text())

    def test_registry_render_requires_digests_for_every_service_including_manage(self):
        images = builder.registry_images('ghcr.io/owner/app@sha256:' + 'a' * 64,
                                         'ghcr.io/owner/caddy@sha256:' + 'b' * 64)
        release = {'delivery': 'registry', 'images': images}
        app, bootstrap = prepare.render(self.base, self.settings, self.credentials, release)
        for name, service in app['services'].items():
            key = name if name in ('db', 'caddy') else 'app'
            self.assertEqual(service['image'], images[key]['tag'])
            self.assertEqual(service['pull_policy'], 'missing')
        self.assertEqual(bootstrap['services']['manage']['image'], images['app']['tag'])
        images['app']['tag'] = 'ghcr.io/owner/app:latest'
        with self.assertRaisesRegex(ValueError, 'pinned'):
            prepare.render(self.base, self.settings, self.credentials, release)
        with self.assertRaises(ValueError):
            builder.registry_images('ghcr.io/owner/app:latest', 'ghcr.io/owner/caddy:latest')

    def test_registry_kit_has_only_allowlisted_files_and_no_image_archive_or_secrets(self):
        with TemporaryDirectory() as folder:
            destination = Path(folder) / 'kit'
            destination.mkdir()
            archive_path = Path(folder) / 'kit.tar.gz'
            release = {'delivery': 'registry', 'images': builder.registry_images(
                'ghcr.io/owner/app@sha256:' + 'a' * 64, 'ghcr.io/owner/caddy@sha256:' + 'b' * 64)}
            builder.write_kit(destination, archive_path, self.base, release)
            expected = {'release.json', 'compose.base.json', 'prepare.py', 'control.py', 'maintenance.py',
                        'settings.example.json', 'init-db.sh', 'README.md', 'SHA256SUMS'}
            self.assertEqual({path.name for path in destination.iterdir()}, expected)
            with tarfile.open(archive_path) as archive:
                self.assertEqual({member.name for member in archive.getmembers()}, {'kit'} | {'kit/' + name for name in expected})
            for line in (destination / 'SHA256SUMS').read_text().splitlines():
                digest, name = line.split('  ', 1)
                self.assertEqual(builder.checksum(destination / name), digest)
            self.assertLess(archive_path.stat().st_size, 100000)

    def test_new_mount_target_requires_explicit_mapping(self):
        self.base['services']['web']['volumes'][0]['target'] = '/unexpected'
        with self.assertRaisesRegex(ValueError, 'Unrecognized mount'):
            prepare.render(self.base, self.settings, self.credentials, self.release)


class TrueNASOperatorTests(TestCase):
    def test_image_identity_accepts_classic_and_containerd_formats_but_rejects_other_images(self):
        entry = {'id': 'sha256:index', 'accepted_ids': ['sha256:index', 'sha256:manifest', 'sha256:config']}
        for identity in entry['accepted_ids']:
            control.verify_image({'Id': identity, 'Architecture': 'amd64', 'Os': 'linux'}, entry)
        for identity, arch in (('sha256:other', 'amd64'), ('sha256:config', 'arm64')):
            with self.assertRaises(RuntimeError):
                control.verify_image({'Id': identity, 'Architecture': arch, 'Os': 'linux'}, entry)

    def test_registry_pull_checks_digest_and_platform_without_starting_services(self):
        reference = 'ghcr.io/owner/app@sha256:' + 'a' * 64
        entry = {'tag': reference, 'identity': 'repo-digest'}
        info = {'Id': 'sha256:config', 'Architecture': 'amd64', 'Os': 'linux', 'RepoDigests': [reference]}
        with patch.object(control, 'run', side_effect=['', json.dumps(info)]) as run:
            control.pull_images({'delivery': 'registry', 'images': {'app': entry}})
            self.assertEqual(run.call_args_list[0].args[0], ['docker', 'pull', '--platform', 'linux/amd64', reference])
            self.assertEqual(len(run.call_args_list), 2)
        for invalid in (dict(info, RepoDigests=[]), dict(info, Architecture='arm64')):
            with self.assertRaises(RuntimeError):
                control.verify_image(invalid, entry)
        with patch.object(control, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Offline'):
                control.pull_images({'images': {'app': entry}})
            run.assert_not_called()
        entry = {'identity': 'repo-digest', 'tag': builder.POSTGRES}
        info['RepoDigests'] = ['docker.io/library/postgres@' + builder.POSTGRES.split('@')[1]]
        control.verify_image(info, entry)

    @patch.object(control, 'run', side_effect=['abc123', 'ix-custom-name'])
    def test_project_is_discovered_from_only_matching_database(self, run):
        self.assertEqual(control.discover_project('installation'), 'ix-custom-name')
        self.assertIn('label=io.sopds.installation=installation', run.call_args_list[0].args[0])

    def test_missing_or_ambiguous_installation_cannot_create_another_project(self):
        for value in ('', 'one\ntwo'):
            with patch.object(control, 'run', return_value=value) as run:
                with self.assertRaises(RuntimeError):
                    control.discover_project('installation')
                self.assertEqual(run.call_count, 1)

    @patch.object(control, 'run', side_effect=['db', '24'])
    def test_init_never_migrates_or_loads_fixtures_into_existing_database(self, run):
        with self.assertRaisesRegex(RuntimeError, 'not empty'):
            control.initialize(['docker', 'compose'])
        self.assertEqual(run.call_count, 2)

    @patch.object(control, 'run', return_value='db\nscanner')
    def test_init_refuses_active_workers(self, run):
        with self.assertRaisesRegex(RuntimeError, 'only db'):
            control.initialize(['docker', 'compose'])
        self.assertEqual(run.call_count, 1)

    def test_empty_database_is_explicitly_initialized_without_starting_services(self):
        responses = iter(['db', '0'])
        with patch.object(control, 'run', side_effect=lambda _: next(responses, '')) as run:
            control.initialize(['docker', 'compose'])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any(command[-2:] == ['loaddata', 'genre.json'] for command in commands))
        self.assertFalse(any('up' in command or 'clear' in command for command in commands))


class PortableBackupTests(TestCase):
    def test_backup_uses_selected_compose_project_and_copies_recovery_configuration(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            env = root / 'private.env'; env.write_text('secret')
            compose = root / 'app.yaml'; compose.write_text('{}')
            release = root / 'release.json'
            metadata = {'commit': 'abc', 'uv_lock_sha256': 'def', 'git_working_tree_dirty': True}
            release.write_text(json.dumps(metadata))
            extra = root / 'settings.json'; extra.write_text('{}')
            args = SimpleNamespace(env_file=env, compose_file=[compose], project_name='ix-sopds',
                                   amd64=False, release_file=release, extra_config=[extra])
            command, files = maintenance.compose_command(args)
            self.assertIn('ix-sopds', command)
            self.assertNotIn('sopds-container', command)
            self.assertIn(str(compose), command)
            with patch.object(maintenance, 'run', side_effect=AssertionError('Git must not be needed on NAS')):
                self.assertEqual(maintenance.source_metadata(args), metadata)
            destination = root / 'backup'; destination.mkdir()
            maintenance.save_configuration(args, files, destination)
            self.assertEqual((destination / 'runtime.env').read_text(), 'secret')
            self.assertEqual((destination / 'compose-0.yaml').read_text(), '{}')
            self.assertTrue((destination / 'extra-settings.json').is_file())
            for path in destination.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_existing_local_backup_default_is_preserved(self):
        args = SimpleNamespace(env_file=Path('/tmp/test.env'), compose_file=None,
                               project_name='sopds-container', amd64=True)
        command, files = maintenance.compose_command(args)
        self.assertEqual(files, [ROOT / 'infra/docker/compose.yaml', ROOT / 'infra/docker/compose.amd64.yaml'])
        self.assertIn('sopds-container', command)

    def test_ambiguous_override_is_rejected(self):
        args = SimpleNamespace(compose_file=[Path('/tmp/app.yaml')], amd64=True)
        with self.assertRaises(ValueError):
            maintenance.compose_command(args)


class BackupRecoveryTests(TestCase):
    def test_verification_failure_restarts_only_previously_running_services(self):
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch), TemporaryDirectory() as folder:
                root = Path(folder)
                backups = root / 'backups'; backups.mkdir()
                args = SimpleNamespace(env_file=root / 'runtime.env', compose_file=[root / 'app.yaml'],
                    project_name='ix-sopds', amd64=False, release_file=None, extra_config=[],
                    database_only=False, status_dir=root / 'status', retain=7)
                args.env_file.write_text('private')
                args.compose_file[0].write_text('{}')
                calls = []

                def fake_run(command, *, output=None, data=None):
                    calls.append(command)
                    if output is not None:
                        if 'tar' in command:
                            with tarfile.open(fileobj=output, mode='w'):
                                pass
                        else:
                            output.write(b'dump')
                        return None
                    if '--services' in command:
                        return b'db web caddy scanner'
                    if 'inspect' in command or '-q' in command:
                        return b'image-id'
                    if 'psql' in command:
                        if 'pg_tables' in command[-1]:
                            return b'book\n'
                        restored = any(str(part).startswith('sopds_restore_') for part in command)
                        return b'2:different' if mismatch and restored else b'1:same'
                    return b''

                with patch.object(maintenance, 'source_metadata', return_value={'commit': 'test'}), \
                     patch.object(maintenance, 'run', side_effect=fake_run), \
                     patch.object(maintenance.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
                    if mismatch:
                        with self.assertRaisesRegex(RuntimeError, 'fingerprint mismatch'):
                            maintenance.perform_backup(args, backups)
                    else:
                        maintenance.perform_backup(args, backups)
                starts = [command for command in calls if 'start' in command]
                self.assertEqual(len(starts), 1)
                self.assertEqual(starts[0][-4:], ['start', 'scanner', 'web', 'caddy'])
                self.assertTrue(any('dropdb' in command for command in calls))
                self.assertEqual((args.status_dir / 'latest.json').exists(), not mismatch)
                self.assertEqual(len(list(backups.glob('sopds-*/manifest.json'))), 0 if mismatch else 1)


class PortableImageTests(TestCase):
    def test_archive_provides_config_and_manifest_identity_for_classic_docker(self):
        with TemporaryDirectory() as folder:
            archive_path = Path(folder) / 'images.tar'
            config = b'{"architecture":"amd64"}'
            config_hash = hashlib.sha256(config).hexdigest()
            manifest = json.dumps({'config': {'digest': 'sha256:' + config_hash}}).encode()
            manifest_hash = hashlib.sha256(manifest).hexdigest()
            catalog = json.dumps([{'RepoTags': ['sopds-app:test'], 'Config': 'blobs/sha256/' + config_hash}]).encode()
            with tarfile.open(archive_path, 'w') as archive:
                for name, data in (('manifest.json', catalog), ('blobs/sha256/' + config_hash, config),
                                   ('blobs/sha256/' + manifest_hash, manifest)):
                    member = tarfile.TarInfo(name); member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            images = {'app': {'tag': 'sopds-app:test', 'id': 'sha256:index'}}
            builder.add_portable_image_ids(archive_path, images)
            self.assertEqual(set(images['app']['accepted_ids']),
                             {'sha256:index', 'sha256:' + config_hash, 'sha256:' + manifest_hash})


class RegistryPrivacyTests(TestCase):
    def test_only_private_or_missing_before_first_publication_is_allowed(self):
        with patch.object(registry, 'request_json', return_value={'visibility': 'private'}):
            registry.check_package('owner', 'app', 'test-token')
        for visibility in ('public', 'internal', None):
            with patch.object(registry, 'request_json', return_value={'visibility': visibility}):
                with self.assertRaises(RuntimeError):
                    registry.check_package('owner', 'app', 'test-token', allow_missing=True)
        for status in (401, 403, 404, 500):
            error = HTTPError('https://example.invalid', status, 'test', {}, None)
            with patch.object(registry, 'request_json', side_effect=error):
                if status == 404:
                    registry.check_package('owner', 'app', 'test-token', allow_missing=True)
                else:
                    with self.assertRaises(RuntimeError):
                        registry.check_package('owner', 'app', 'test-token', allow_missing=True)
                with self.assertRaises(RuntimeError):
                    registry.check_package('owner', 'app', 'test-token')

    def test_anonymous_check_requires_authorization_denial_not_network_failure(self):
        reference = 'ghcr.io/owner/app@sha256:' + 'a' * 64
        for status in (401, 403, 404, 500):
            with patch.object(registry, 'request_json', side_effect=HTTPError('url', status, 'test', {}, None)):
                if status in (401, 403):
                    registry.check_anonymous_denied(reference)
                else:
                    with self.assertRaises(RuntimeError):
                        registry.check_anonymous_denied(reference)
        with patch.object(registry, 'request_json', return_value={'token': 'anonymous-test-token'}), patch.object(registry, 'urlopen'):
            with self.assertRaisesRegex(RuntimeError, 'allowed'):
                registry.check_anonymous_denied(reference)

    def test_initialization_never_uploads_application_content_before_private_check(self):
        def build(command, check):
            root = Path(command[-1])
            self.assertEqual({path.name for path in root.iterdir()}, {'Dockerfile'})
            self.assertEqual((root / 'Dockerfile').read_text(), 'FROM scratch\nLABEL io.sopds.purpose="empty registry privacy probe"\n')
            metadata = Path(command[command.index('--metadata-file') + 1])
            metadata.write_text(json.dumps({'containerimage.digest': 'sha256:' + 'a' * 64}))
        with patch.object(registry, 'check_package', side_effect=[False, RuntimeError('public')]) as visibility, \
                patch.object(registry.subprocess, 'run', side_effect=build), \
                patch.object(registry, 'check_anonymous_denied') as anonymous:
            with self.assertRaisesRegex(RuntimeError, 'public'):
                registry.initialize_private_package('owner', 'app', 'test-token')
            self.assertEqual(visibility.call_count, 2)
            anonymous.assert_not_called()
        with patch.object(registry, 'check_package', return_value=True), patch.object(registry.subprocess, 'run') as build:
            registry.initialize_private_package('owner', 'app', 'test-token')
            build.assert_not_called()


class StandaloneComposeTests(TestCase):
    def test_renderer_rejects_mutable_or_wrong_packages_and_couples_readiness_to_digest(self):
        renderer = load('truenas_renderer', 'scripts/truenas/render-compose.py')
        app = 'ghcr.io/denbond007/sopds-nas-app@sha256:' + 'a' * 64
        caddy = 'ghcr.io/denbond007/sopds-nas-caddy@sha256:' + 'b' * 64
        text = renderer.render(app, caddy)
        self.assertNotIn('__APP_', text)
        self.assertNotIn('__CADDY_', text)
        self.assertIn(app, text)
        self.assertIn(caddy, text)
        self.assertIn('SOPDS_RELEASE: sha256:' + 'a' * 64, text)
        for invalid in (app.split('@')[0] + ':latest', app.replace('denbond007', 'other'), caddy):
            with self.assertRaises(ValueError):
                renderer.render(invalid, caddy)

    def test_compose_has_only_self_contained_mounts_and_fail_closed_dependencies(self):
        import subprocess
        renderer = load('truenas_renderer_config', 'scripts/truenas/render-compose.py')
        text = renderer.render('ghcr.io/denbond007/sopds-nas-app@sha256:' + 'a' * 64,
                               'ghcr.io/denbond007/sopds-nas-caddy@sha256:' + 'b' * 64)
        result = subprocess.run(['docker', 'compose', '-f', '-', 'config', '--format', 'json'],
                                input=text, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(text.count('create_host_path: false'), 5)
        self.assertNotIn('create_host_path: true', text)
        services = config['services']
        self.assertEqual(set(services), {'db', 'init', 'web', 'scanner', 'annotations', 'caddy'})
        for name, service in services.items():
            self.assertIn('@sha256:', service['image'])
            self.assertEqual(service['platform'], 'linux/amd64')
            self.assertEqual(service['pull_policy'], 'missing')
            self.assertNotIn('build', service)
            self.assertNotIn('env_file', service)
            self.assertNotIn('profiles', service)
            for mount in service['volumes']:
                self.assertEqual(mount['type'], 'bind')
                self.assertTrue(mount['source'].startswith('/mnt/'))
                # Compose v2 omits false booleans from normalized JSON.
                self.assertFalse(mount.get('bind', {}).get('create_host_path', False))
            if name != 'db':
                self.assertEqual(service['user'], '10001:10001')
                self.assertTrue(service['read_only'])
        for name in ('web', 'scanner', 'annotations', 'caddy'):
            self.assertEqual(services[name]['depends_on']['init']['condition'], 'service_completed_successfully')
            self.assertNotIn('POSTGRES_PASSWORD', services[name]['environment'])
            self.assertNotIn('SOPDS_ADMIN_PASSWORD', services[name]['environment'])
            self.assertEqual(services[name]['environment']['SOPDS_REQUIRE_INITIALIZED'], '1')
        self.assertEqual(services['init']['depends_on']['db']['condition'], 'service_healthy')
        self.assertEqual(services['init']['restart'], 'no')
        self.assertEqual(services['init']['environment']['SOPDS_REQUIRE_INITIALIZED'], '0')
        for name in ('init', 'web', 'scanner', 'annotations'):
            books = next(m for m in services[name]['volumes'] if m['target'] == '/library')
            self.assertTrue(books['read_only'])
        self.assertEqual(services['caddy']['depends_on']['web']['condition'], 'service_healthy')
        self.assertIn('exec /usr/local/bin/docker-entrypoint.sh postgres', services['db']['entrypoint'][-1])
        self.assertIn('POSTGRES_PASSWORD', services['db']['entrypoint'][-1])
        self.assertNotIn('ports', services['db'])
        self.assertNotIn('ports', services['web'])
        self.assertTrue(config['networks']['backend']['internal'])
