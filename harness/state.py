"""Crash-resistant ownership ledger. No API or infrastructure calls."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

KINDS = ('sandboxes', 'conversations', 'automations', 'start_tasks')


def atomic_json(path, value):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


class Ledger:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder/'owned.json'

    @contextlib.contextmanager
    def locked(self):
        with (self.folder/'owned.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def read(self):
        with self.locked(): return json.loads(self.path.read_text())

    def update(self, fn):
        with self.locked():
            d = json.loads(self.path.read_text()); result = fn(d); atomic_json(self.path, d)
            return result

    def initialize(self, baseline, run_id, started=None):
        with self.locked():
            if self.path.exists(): raise ValueError('Ownership ledger already exists')
            atomic_json(self.path, {**{k:[] for k in KINDS}, 'baseline':baseline,
                        'run_id':run_id, 'started':started or time.time(), 'intents':{}, 'proofs':{}})

    def intent(self, kind, label):
        nonce = uuid.uuid4().hex
        def add(d):
            d['intents'][nonce] = {'kind':kind, 'label':label, 'time':time.time(), 'resolved':False}
        self.update(add)
        return nonce

    def claim(self, kind, row, intent, association=None):
        ident = row['id']
        def add(d):
            if ident in d['baseline'].get(kind, []): raise ValueError('Refusing pre-existing resource')
            request = d['intents'].get(intent)
            if not request: raise ValueError('No recorded creation intent')
            # Association permits a newly created runtime from our exact automation run.
            if request['kind'] != kind and not association: raise ValueError('Creation kind mismatch')
            if association:
                from datetime import datetime
                created = row.get('created_at')
                stamp = datetime.fromisoformat(created.replace('Z','+00:00')).timestamp() if created else 0
                if stamp < request['time']-1: raise ValueError('Reused or undated resource')
            key = kind+'/'+ident
            existing = d['proofs'].get(key)
            if existing and existing['intent'] != intent: raise ValueError('Resource already claimed by another operation')
            if ident not in d[kind]: d[kind].append(ident)
            d['proofs'][key] = {'intent':intent, 'association':association, 'observed_new':True}
            if request['kind'] == kind: request.update(resolved=True, resource_id=ident)
        self.update(add)
        return ident

    def deletable(self, kind, ident):
        d = self.read()
        return ident in d.get(kind, []) and ident not in d['baseline'].get(kind, []) and d['proofs'].get(kind+'/'+ident, {}).get('observed_new') is True

    def unresolved(self):
        return [k for k,v in self.read()['intents'].items() if not v['resolved']]
