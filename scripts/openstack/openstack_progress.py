"""Presentation only: one stderr reporter, no remote probes or transaction writes.

Unknown labels, payloads and log contents are never rendered. The timer observes
only explicitly registered local files; liveness is not proof of remote progress.
"""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import stat
import sys
import threading
import time

LABELS = {
 'install': 'OpenStack installation', 'add': 'Compute addition', 'remove': 'Compute removal',
 'check-install': 'Checking OpenStack installation', 'check-add': 'Checking compute addition',
 'check-remove': 'Checking compute removal',
 'inputs': 'Preparing and verifying installation inputs', 'scan': 'Checking all selected hosts',
 'host-check': 'Checking access, services and host state',
 'install-artifacts': 'Building matched Nova and Skyline images',
 'install-workspace': 'Preparing Kolla configuration and certificates',
 'install-compute-binding': 'Preparing disabled compute configuration',
 'install-docker': 'Preparing Docker on all hosts', 'install-network': 'Preparing host networking',
 'install-helpers': 'Installing protected host helpers', 'install-images': 'Delivering verified images',
 'install-dependencies': 'Preparing host dependencies and API trust',
 'install-kolla': 'Deploying OpenStack with Kolla',
 'install-platform': 'Registering computes and checking OpenStack APIs and GUI',
 'cloud-fetch': 'Retrieving and verifying the controller cloud kit',
 'cloud-environment': 'Verifying the local prepared environment',
 'cloud-snapshot': 'Preparing the durable controller cloud kit',
 'cloud-upload': 'Sending and committing the controller cloud kit',
 'cloud-claim': 'Claiming the approved cloud operation',
 'controller-claim': 'Claiming the approved controller operation',
 'reconcile-owned-metadata': 'Removing proven retired-host metadata',
 'host-preparation': 'Preparing the compute host',
 'executor': 'Installing protected host management', 'compute': 'Deploying and registering compute',
 'configuration': 'Updating the controller host configuration',
 'catalogs': 'Refreshing host resource catalogs',
 'refresh-catalogs': 'Refreshing remaining host resource catalogs',
 'controller-completion': 'Confirming controller completion',
 'controller-prepare': 'Installing controller components',
 'controller-accept': 'Checking controller APIs and completing compute enrollment',
 'cloud-completion': 'Saving the authoritative controller cloud kit',
 'peer-network': 'Updating owned inter-host network allowlists',
 'hostnames': 'Preparing host name resolution', 'dependencies': 'Preparing host dependencies',
 'container-engine': 'Preparing Docker', 'network': 'Preparing compute networking',
 'helper': 'Installing the protected host helper', 'driver-image': 'Delivering the verified compute image',
 'kolla-prechecks': 'Running Kolla prechecks', 'kolla-deploy': 'Deploying OpenStack services',
 'kolla-post-deploy': 'Completing Kolla API configuration',
 'compute-deploy': 'Deploying Nova compute', 'compute-enroll': 'Registering and checking compute',
 'quarantine': 'Disabling new VM admissions', 'stop': 'Stopping managed services on the empty target',
 'enroll': 'Binding verified helper and Nova configuration', 'start': 'Starting managed compute services',
 'accept': 'Checking compute and network readiness',
 'enable': 'Confirming protected resource and scheduling state',
 'stage': 'Preparing verified compute files', 'genconfig': 'Generating compute configuration',
 'restart': 'Applying compute configuration',
 'executor-revocation': 'Revoking the retired host management identity',
 'native-removal': 'Removing Nova, Placement and OVN registrations',
 'build-compute': 'Building the matched Nova compute image',
 'build-console': 'Building the matched Skyline console image',
 'prepare-launcher': 'Preparing the verified VM launcher',
 'base-packages': 'Preparing system prerequisites', 'host-sdk': 'Preparing the host SDK',
 'api-trust': 'Installing the verified API CA',
}
_active = None


def duration(seconds):
    seconds = max(0, int(seconds))
    return '%02d:%02d' % (seconds // 60, seconds % 60)


def safe(value):
    return str(value).encode('unicode_escape').decode('ascii')[:512]


class Reporter:
    def __init__(self, *, stream=None, clock=time.monotonic, interval=60, threaded=True, journal=None):
        self.stream = stream if stream is not None else sys.stderr
        self.clock, self.interval, self.threaded = clock, interval, threaded
        self.lock = threading.RLock(); self.stop = threading.Event()
        self.stack = []; self.audits = set(); self.paused = 0; self.thread = None; self.sequence = 0
        self.last_output = clock()
        self.journal = journal

    def emit(self, kind, frame, suffix=''):
        with self.lock:
            prefix = (safe(frame['host']) + ': ') if frame['host'] else ''
            print('[' + kind + '] ' + prefix + frame['label'] + suffix, file=self.stream, flush=True)
            self.last_output = self.clock()

    @contextmanager
    def step(self, key, *, host=None, log=None, quiet=False):
        # Fixed labels only. No exception, command, RPC frame or file body here.
        label = LABELS[key]
        now = self.clock()
        with self.lock:
            self.sequence += 1
            inherited = self.stack[-1]['host'] if self.stack else None
            frame = {'key': key, 'label': label, 'host': host or inherited, 'start': now,
                     'number': self.sequence,
                     'activity': now, 'evidence': 'response', 'bytes': 0, 'total': None,
                     'log': Path(log) if log else None, 'fingerprint': None}
            if frame['log']:
                try:
                    st = frame['log'].lstat()
                    if stat.S_ISREG(st.st_mode):frame['fingerprint'] = (st.st_ino, st.st_size, st.st_mtime_ns)
                except OSError:pass
            self.stack.append(frame)
            if not quiet: self.emit('RUN', frame)
        try:
            yield frame
        except BaseException:
            with self.lock:
                detail = ' — stopped after ' + duration(self.clock() - now)
                path = frame['log'] or self.journal
                if path: detail += '; private log/journal: ' + safe(path)
                self.emit('ERROR', frame, detail)
            raise
        else:
            if not quiet and not frame.get('failed'):
                with self.lock:self.emit('OK', frame, ' — ' + duration(self.clock() - now))
        finally:
            with self.lock:
                assert self.stack[-1] is frame
                self.stack.pop()
                if self.stack:self.stack[-1]['activity'] = self.clock()

    @contextmanager
    def pause(self):
        with self.lock:self.paused += 1
        try:yield
        finally:
            with self.lock:self.paused -= 1; self.last_output = self.clock()

    def observe(self):
        # Log metadata, not its potentially credential-bearing contents. No
        # pathname traversal or recursive scanning of arbitrary work directories.
        for frame in self.stack:
            if not frame['log']:continue
            try:
                st = frame['log'].lstat()
                if not stat.S_ISREG(st.st_mode):continue
                fingerprint = (st.st_ino, st.st_size, st.st_mtime_ns)
            except OSError:continue
            if frame['fingerprint'] != fingerprint:
                frame.update(fingerprint=fingerprint, activity=self.clock(), evidence='log')

    def tick(self):
        with self.lock:
            self.observe()
            if self.paused or not self.stack or self.clock() - self.last_output < self.interval:return
            frame = self.stack[-1]; age = duration(self.clock() - frame['activity'])
            if frame['evidence'] == 'transfer':
                detail = '; sent/received %d bytes' % frame['bytes']
                if frame['total'] is not None:detail += ' of %d' % frame['total']
                detail += '; last transfer update ' + age + ' ago'
            elif frame['evidence'] == 'log':detail = '; local log updated ' + age + ' ago'
            else:detail = '; awaiting response; last confirmed update ' + age + ' ago'
            self.emit('WAIT', frame, ' — elapsed ' + duration(self.clock() - frame['start']) + detail)

    def run(self):
        while not self.stop.wait(1):self.tick()

    def start(self):
        if self.threaded:
            self.thread = threading.Thread(target=self.run, name='spvm-progress', daemon=True)
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=2)


@contextmanager
def step(key, *, host=None, log=None, quiet=False):
    if _active is None:yield None
    else:
        with _active.step(key, host=host, log=log, quiet=quiet) as frame:yield frame


def phase(key, *, host=None, log=None):
    def decorate(fn):
        @wraps(fn)
        def call(*args, **kwargs):
            if _active is None:return fn(*args, **kwargs)
            h = host(*args, **kwargs) if callable(host) else host
            p = log(*args, **kwargs) if callable(log) else log
            k = key(*args, **kwargs) if callable(key) else key
            with step(k, host=h, log=p):return fn(*args, **kwargs)
        return call
    return decorate


@contextmanager
def pause():
    if _active is None:yield
    else:
        with _active.pause():yield


@contextmanager
def audit(host):
    if _active is None:yield; return
    with _active.lock:
        parent = _active.stack[-1] if _active.stack else None
        token = (parent['number'] if parent else None, host)
        first = token not in _active.audits
        _active.audits.add(token)
    with step('host-check', host=host, quiet=True) as frame:
        if first:_active.emit('CHECK',frame)
        yield


class CountingStream:
    """Transparent byte accounting; does not inject bytes or close its stream."""
    def __init__(self, stream, *, total=None):
        self.stream, self.total = stream, total
        self.reporter = _active
        self.frame = self.reporter.stack[-1] if self.reporter and self.reporter.stack else None

    def count(self, size):
        if self.frame and size:
            with self.reporter.lock:
                self.frame.update(evidence='transfer', activity=self.reporter.clock(),
                                  bytes=self.frame['bytes'] + size, total=self.total)

    def read(self, size=-1):
        value = self.stream.read(size); self.count(len(value)); return value

    def write(self, value):
        size = self.stream.write(value); self.count(size if size is not None else len(value)); return size

    def __getattr__(self, name):return getattr(self.stream, name)

    def finished(self):
        if self.frame:
            with self.reporter.lock:self.frame['evidence'] = 'response'


def command(key):
    def decorate(fn):
        @wraps(fn)
        def call(argv=None):
            global _active
            if _active is not None:return fn(argv)
            args = list(sys.argv[1:] if argv is None else argv)
            if '--help' in args or '-h' in args:return fn(argv)
            output = next((a.split('=',1)[1] for a in args if a.startswith('--output=')), None)
            if '--output' in args and args.index('--output')+1<len(args):output=args[args.index('--output')+1]
            journal = Path(output)/{'install':'installation.json','add':'batch.json','remove':'transaction.json'}[key] if output else None
            reporter = Reporter(journal=journal); _active = reporter; reporter.start()
            try:
                with step('check-'+key if '--check' in args else key) as frame:
                    result = fn(argv)
                    # Main entrypoints already handle/report errors, so don't
                    # print a successful root phase just because they returned.
                    if result not in (None, 0):
                        frame['failed'] = True
                        detail = ' — stopped after '+duration(reporter.clock()-frame['start'])
                        if journal:detail += '; private journal: '+safe(journal)
                        reporter.emit('ERROR', frame, detail)
                        return result
                    return result
            finally:reporter.close(); _active = None
        return call
    return decorate
