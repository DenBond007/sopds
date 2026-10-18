import os
import signal
import sys
import logging
from contextlib import nullcontext
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler


from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.conf import settings as main_settings

from opds_catalog.models import Counter
from opds_catalog.health import record_service
from opds_catalog.sopdscan import opdsScanner
#from opds_catalog.settings import SCANNER_LOG, SCAN_SHED_DAY, SCAN_SHED_DOW, SCAN_SHED_HOUR, SCAN_SHED_MIN, LOGLEVEL, SCANNER_PID
from opds_catalog import settings 
from constance import config
from opds_catalog.scanner_process import scanner_pidfile, stop_scanner

SCAN_LOCK_KEYS = (0x534F5044, 0x5343414E)  # "SOPD", "SCAN"

class Command(BaseCommand):
    help = 'Scan Books Collection.'
    scan_is_active = False

    def add_arguments(self, parser):
        parser.add_argument('command', choices=['scan', 'run', 'start', 'stop', 'restart'])
        parser.add_argument('--verbose',action='store_true', dest='verbose', default=False, help='Set verbosity level for books collection scan.')
        parser.add_argument('--daemon',action='store_true', dest='daemonize', default=False, help='Daemonize server')
        
    def handle(self, *args, **options): 
        self.pidfile = os.path.join(main_settings.BASE_DIR, config.SOPDS_SCANNER_PID)
        action = options['command']
        if action == 'run' and options['daemonize']:
            raise CommandError('run stays in the foreground; do not use --daemon')
        self.logger = logging.getLogger('')
        self.logger.setLevel(logging.DEBUG)
        formatter=logging.Formatter('%(asctime)s %(levelname)-8s %(message)s')

        if action != 'run' and settings.LOGLEVEL!=logging.NOTSET:
            # Создаем обработчик для записи логов в файл
            fh = logging.FileHandler(config.SOPDS_SCANNER_LOG)
            fh.setLevel(settings.LOGLEVEL)
            fh.setFormatter(formatter)
            self.logger.addHandler(fh)

        if options['verbose'] or action == 'run':
            # Создадим обработчик для вывода логов на экран с максимальным уровнем вывода
            ch = logging.StreamHandler()
            ch.setLevel(logging.DEBUG)
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)
            
        if (options["daemonize"] and (action in ["start", "scan"])):
            if sys.platform == "win32":
                self.stdout.write("On Windows platform Daemonize not working.")
            else:         
                daemonize()            

        if action=='scan':
            self.stdout.write('Startup once book-scan.')
            self.scan()   
            self.stdout.write('Complete book-scan.')        
        elif action == 'run':
            self.start(supervised=True)
        elif action == "start":
            self.start()
        elif action == "stop":
            pid = Path(self.pidfile).read_text()
            self.stop(pid)
        elif action == "restart":
            pid = Path(self.pidfile).read_text()
            self.restart(pid)            

    def scan(self):
        if self.scan_is_active:
            self.stdout.write('Scan process already active. Skip currend job.')
            return

        self.scan_is_active = True
        lock_acquired = False
        try:
            if connection.connection and not connection.is_usable():
                connection.close()
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_try_advisory_lock(%s, %s)', SCAN_LOCK_KEYS)
                lock_acquired = cursor.fetchone()[0]
            if not lock_acquired:
                self.stdout.write('Another scan process is active. Skip current job.')
                return

            record_service('scanner', 'running')
            scanner = opdsScanner(self.logger)
            scanner.scan_all()
            Counter.objects.update_known_counters()
            details = {key: getattr(scanner, key, 0) for key in (
                'books_added', 'books_updated', 'books_skipped', 'bad_books', 'bad_archives')}
            degraded = details['bad_books'] or details['bad_archives']
            record_service('scanner', 'degraded' if degraded else 'idle', success=True, details=details)
        except BaseException as error:
            if lock_acquired:
                try:
                    record_service('scanner', 'error', error=type(error).__name__)
                except Exception:
                    self.logger.exception('Could not record scanner failure')
            raise
        finally:
            try:
                if lock_acquired:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_advisory_unlock(%s, %s)', SCAN_LOCK_KEYS)
            finally:
                self.scan_is_active = False

    def update_shedule(self):
        self.SCAN_SHED_DAY = config.SOPDS_SCAN_SHED_DAY
        self.SCAN_SHED_DOW = config.SOPDS_SCAN_SHED_DOW
        self.SCAN_SHED_HOUR = config.SOPDS_SCAN_SHED_HOUR
        self.SCAN_SHED_MIN = config.SOPDS_SCAN_SHED_MIN
        self.stdout.write('Reconfigure scheduled book-scan (min=%s, hour=%s, day_of_week=%s, day=%s).'%(self.SCAN_SHED_MIN,self.SCAN_SHED_HOUR,self.SCAN_SHED_DOW,self.SCAN_SHED_DAY))
        self.sched.reschedule_job('scan', trigger='cron', day=self.SCAN_SHED_DAY, day_of_week=self.SCAN_SHED_DOW, hour=self.SCAN_SHED_HOUR, minute=self.SCAN_SHED_MIN)
    
    def check_settings(self):
        if getattr(self, '_stopping', False):
            return
        if connection.connection and not connection.is_usable():
            connection.close()
        record_service('scanner')
        settings.constance_update_all()
        if not (self.SCAN_SHED_MIN==config.SOPDS_SCAN_SHED_MIN and \
           self.SCAN_SHED_HOUR==config.SOPDS_SCAN_SHED_HOUR and \
           self.SCAN_SHED_DOW==config.SOPDS_SCAN_SHED_DOW and \
           self.SCAN_SHED_DAY==config.SOPDS_SCAN_SHED_DAY):
            self.update_shedule()
        if config.SOPDS_SCAN_START_DIRECTLY:
            config.SOPDS_SCAN_START_DIRECTLY = False
            self.stdout.write('Startup scannyng directly by SOPDS_SCAN_START_DIRECTLY flag.')
            self.sched.add_job(self.scan, id='scan_directly')
                       
    def start(self, *, supervised=False):
        self.SCAN_SHED_DAY = config.SOPDS_SCAN_SHED_DAY
        self.SCAN_SHED_DOW = config.SOPDS_SCAN_SHED_DOW
        self.SCAN_SHED_HOUR = config.SOPDS_SCAN_SHED_HOUR
        self.SCAN_SHED_MIN = config.SOPDS_SCAN_SHED_MIN
        self.sched = BlockingScheduler()
        self.sched.add_job(
            self.scan, 'cron', day=self.SCAN_SHED_DAY,
            day_of_week=self.SCAN_SHED_DOW, hour=self.SCAN_SHED_HOUR,
            minute=self.SCAN_SHED_MIN, id='scan',
        )
        self.sched.add_job(self.check_settings, 'cron', minute='*/10', id='check')
        self._stopping = False

        class StopScheduler(BaseException):
            pass

        def request_stop(signum, frame):
            if not self._stopping:
                self._stopping = True
                raise StopScheduler()

        previous = {}
        ownership = nullcontext() if supervised else scanner_pidfile(self.pidfile)
        with ownership:
            try:
                for signum in (signal.SIGTERM, signal.SIGINT):
                    previous[signum] = signal.signal(signum, request_stop)
                record_service('scanner', 'idle')
                self.stdout.write('Scanner ready; SIGTERM/SIGINT finish the active scan.')
                self.sched.start()
            except (StopScheduler, KeyboardInterrupt):
                pass
            finally:
                try:
                    if self.sched.running:
                        self.sched.shutdown(wait=True)
                finally:
                    for signum, handler in previous.items():
                        signal.signal(signum, handler)

    def stop(self, record):
        stop_scanner(record)

    def restart(self, record):
        self.stop(record)
        self.start()


def daemonize():
    """
    Detach from the terminal and continue as a daemon.
    """
    # swiped from twisted/scripts/twistd.py
    # See http://www.erlenstar.demon.co.uk/unix/faq_toc.html#TOC16
    if os.fork():   # launch child and...
        os._exit(0) # kill off parent
    os.setsid()
    if os.fork():   # launch child and...
        os._exit(0) # kill off parent again.
    os.umask(0o077)

    std_in = open("/dev/null", 'r')
    std_out = open(config.SOPDS_SCANNER_LOG, 'a+')
    os.dup2(std_in.fileno(), sys.stdin.fileno())
    os.dup2(std_out.fileno(), sys.stdout.fileno())
    os.dup2(std_out.fileno(), sys.stderr.fileno())    
    
#    null = os.open("/dev/null", os.O_RDWR)
#    for i in range(3):
#        try:
#            os.dup2(null, i)
#        except OSError as e:
#            if e.errno != errno.EBADF:
#                raise
    os.close(std_in.fileno())
    os.close(std_out.fileno())


    

        
 

