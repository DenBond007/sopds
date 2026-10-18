"""Explicit, restart-safe initialization for the self-contained TrueNAS app."""
from contextlib import closing
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryFile

import psycopg2
from psycopg2 import sql
from constance import settings as constance_settings
from constance.backends.database.models import Constance
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction

# Session locks also cover non-transactional migration/static work.
LOCK_KEYS = (1397706820, 1397313097)
STATE_TABLE = 'sopds_bootstrap_state'
DEFAULTS = {
    'SOPDS_ROOT_LIB': '/library',
    'SOPDS_TEMP_DIR': '/tmp/sopds',
    'SOPDS_SCANNER_LOG': '/tmp/sopds-scanner.log',
    'SOPDS_AUTH': True,
}


def release_id():
    value = os.environ.get('SOPDS_RELEASE', '')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
        raise CommandError('SOPDS_RELEASE must match the pinned application image digest.')
    return value


def credential(name, minimum=32):
    value = os.environ.get(name, '')
    if len(value) < minimum or value.upper().startswith(('CHANGE_ME', 'REPLACE_')):
        raise CommandError('Set a private value for ' + name + ' before initialization.')
    return value


def provision_role(admin):
    """Create the application role only once; never rotate an existing password."""
    if settings.DATABASES['default']['NAME'] != 'sopds' or settings.DATABASES['default']['USER'] != 'sopds':
        raise CommandError('Self-contained initialization requires database/user sopds.')
    with admin:
        with admin.cursor() as cursor:
            cursor.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', ['sopds'])
            if cursor.fetchone():
                return
            cursor.execute("SELECT 1 FROM pg_tables WHERE schemaname = 'public' LIMIT 1")
            if cursor.fetchone():
                raise CommandError('Existing database has no sopds role; restore its role explicitly.')
            cursor.execute(sql.SQL('CREATE ROLE sopds LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION').format(
                sql.Literal(credential('DB_PASS'))))
            cursor.execute('ALTER DATABASE sopds OWNER TO sopds')


def prepare_state():
    """Commit new-install intent BEFORE migrations, so a crash cannot look legacy."""
    with transaction.atomic():
        tables = connection.introspection.table_names()
        if STATE_TABLE not in tables:
            fresh = not tables
            if fresh:
                administrator()  # Validate before committing new-install intent.
            with connection.cursor() as cursor:
                cursor.execute('''CREATE TABLE sopds_bootstrap_state (
                    id smallint PRIMARY KEY CHECK (id = 1),
                    completed boolean NOT NULL,
                    ready_release varchar(71) NOT NULL DEFAULT ''
                )''')
                cursor.execute('INSERT INTO sopds_bootstrap_state (id, completed) VALUES (1, %s)', [not fresh])
        with connection.cursor() as cursor:
            cursor.execute('SELECT completed FROM sopds_bootstrap_state WHERE id = 1 FOR UPDATE')
            row = cursor.fetchone()
            if row is None:
                raise CommandError('Initialization checkpoint is damaged; restore it before retrying.')
            cursor.execute("UPDATE sopds_bootstrap_state SET ready_release = '' WHERE id = 1")
            return not row[0]


def administrator():
    User = get_user_model()
    username = os.environ.get('SOPDS_ADMIN_USERNAME', '')
    email = os.environ.get('SOPDS_ADMIN_EMAIL', '')
    if not username or not email or any(value.upper().startswith(('CHANGE_ME', 'REPLACE_')) for value in (username, email)):
        raise CommandError('Set SOPDS_ADMIN_USERNAME and SOPDS_ADMIN_EMAIL for a new installation.')
    user = User(username=username, email=email, is_staff=True, is_superuser=True)
    password = credential('SOPDS_ADMIN_PASSWORD', minimum=16)
    try:
        # Do not query auth tables before migrate has created them.
        user.clean_fields(exclude=['password'])
        user.clean()
        validate_password(password, user)
    except ValidationError:
        raise CommandError('Initial administrator username/email/password failed validation.') from None
    user.set_password(password)
    return user


def seed_new_library():
    """Genres, defaults, admin and checkpoint are one PostgreSQL transaction."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SELECT completed FROM sopds_bootstrap_state WHERE id = 1 FOR UPDATE')
            if cursor.fetchone()[0]:
                return
        models = [get_user_model(), Constance, *apps.get_app_config('opds_catalog').get_models()]
        if any(model.objects.exists() for model in models):
            raise CommandError('Pending initialization contains library/account/settings data; refusing to overwrite it.')
        user = administrator()
        call_command('loaddata', 'genre.json', verbosity=0)
        for key, value in DEFAULTS.items():
            Constance.objects.create(key=constance_settings.DATABASE_PREFIX + key, value=value)
        user.save()
        with connection.cursor() as cursor:
            cursor.execute('UPDATE sopds_bootstrap_state SET completed = true WHERE id = 1')


def initialize(release):
    pending = prepare_state()
    marker = Path(settings.STATIC_ROOT) / '.sopds-ready'
    marker.unlink(missing_ok=True)
    check_mounts()
    call_command('migrate', interactive=False, verbosity=1, skip_checks=True)
    if pending:
        seed_new_library()
    # URL checks can read Constance and persist defaults; seed must precede them.
    call_command('check', verbosity=0)
    call_command('collectstatic', interactive=False, verbosity=0)
    with NamedTemporaryFile(mode='w', dir=settings.STATIC_ROOT, delete=False) as output:
        temporary = Path(output.name)
        output.write(release + '\n')
    try:
        temporary.replace(marker)
    finally:
        temporary.unlink(missing_ok=True)
    with connection.cursor() as cursor:
        cursor.execute('UPDATE sopds_bootstrap_state SET ready_release = %s WHERE id = 1 AND completed', [release])


def check_mounts():
    library = Path('/library')
    # Fail before migrations if mounts/permissions are wrong; never mkdir/chown books.
    with os.scandir(library) as entries:
        next(entries, None)
    with TemporaryFile(dir=settings.STATIC_ROOT):
        pass


def check_ready():
    release = release_id()
    marker = Path(settings.STATIC_ROOT) / '.sopds-ready'
    if not marker.is_file() or marker.read_text().strip() != release:
        raise CommandError('Static files have not been initialized for this release.')
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock_shared(%s, %s)', LOCK_KEYS)
        if not cursor.fetchone()[0]:
            raise CommandError('Initialization is still running; application startup is blocked.')
        try:
            if STATE_TABLE not in connection.introspection.table_names():
                raise CommandError('Initialization has not succeeded; inspect the init service logs.')
            cursor.execute('SELECT completed, ready_release FROM sopds_bootstrap_state WHERE id = 1')
            if cursor.fetchone() != (True, release):
                raise CommandError('Initialization has not succeeded for this release; inspect the init service logs.')
        finally:
            cursor.execute('SELECT pg_advisory_unlock_shared(%s, %s)', LOCK_KEYS)


def run_initialization():
    release = release_id()
    password = credential('POSTGRES_PASSWORD')
    app_password = credential('DB_PASS')
    secret = credential('SOPDS_SECRET_KEY')
    if len({password, app_password, secret}) != 3:
        raise CommandError('PostgreSQL, application database and Django secrets must be different.')
    database = settings.DATABASES['default']
    # Only this one-shot service receives the PostgreSQL administrator credential.
    with closing(psycopg2.connect(dbname=database['NAME'], user='postgres', password=password,
                          host=database['HOST'], port=database['PORT'], connect_timeout=10)) as admin:
        with admin.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s, %s)', LOCK_KEYS)
            if not cursor.fetchone()[0]:
                raise CommandError('Another initialization is running; wait before retrying.')
        try:
            provision_role(admin)
            initialize(release)
        finally:
            with admin.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s, %s)', LOCK_KEYS)
    connection.close()
