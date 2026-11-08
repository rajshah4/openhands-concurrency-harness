"""Portable, bounded native-agent cohorts. No infrastructure administration."""
import argparse
import base64
import concurrent.futures
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import signal
import threading
import time
import urllib.parse
import uuid

ROOT = Path(__file__).resolve().parent.parent


def validate(config):
    target = config['target']
    origin = urllib.parse.urlsplit(target['origin'])
    if origin.scheme != 'https' or not origin.hostname or origin.path or origin.query or origin.fragment or origin.username or origin.password:
        raise ValueError('Use an HTTPS origin without a path, credentials or query')
    for name in ['org_id', 'user_id']:
        uuid.UUID(target[name])
    if not target.get('sandbox_origins'):
        raise ValueError('Explicit runtime origin allowlist required')
    for value in target['sandbox_origins']:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError('Invalid runtime origin allowlist')
        if '*' in value and not re.fullmatch(r'https://\*\.[a-z0-9.-]+', value):
            raise ValueError('Only one leading wildcard label is supported')
    levels = config['levels']
    if not levels or any(type(n) is not int or n < 1 for n in levels) or levels != sorted(set(levels)):
        raise ValueError('Levels must be positive, increasing integers')
    if not config.get('model_profile'):
        raise ValueError('An existing model profile is required')
    for name in ['wave_timeout_seconds', 'health_failure_limit', 'prepare_workers']:
        if type(config[name]) is not int or config[name] < 1:
            raise ValueError(name+' must be a positive integer')
    if not .5 <= config['maximum_control_requests_per_second'] <= 3:
        raise ValueError('Control request rate must be between 0.5 and 3')
    if not 0 <= config.get('allowed_unsuccessful', 0) < levels[0]:
        raise ValueError('Invalid unsuccessful-job allowance')
    if not 0 < config['max_run_seconds'] <= 86400:
        raise ValueError('Set an explicit run budget up to 24 hours')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',config.get('api_key_env','OPENHANDS_API_KEY')):
        raise ValueError('Invalid credential environment variable name')
    return config


def overlap(rows):
    points = [(r['started'], 1) for r in rows if r.get('passed')]
    points += [(r['finished'], -1) for r in rows if r.get('passed')]
    active = peak = 0
    for _, delta in sorted(points):
        active += delta
        peak = max(peak, active)
    return peak


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', default='runs')
    parser.add_argument('--execute', action='store_true', help='Actually create native workloads')
    args = parser.parse_args()
    config = validate(json.loads(Path(args.config).read_text()))
    if not args.execute:
        print('Configuration valid. No network requests or jobs created. Add --execute to run.')
        return 0
    os.umask(0o077)
    base = Path(args.output).resolve()
    lock_dir = base/'.locks'
    lock_dir.mkdir(parents=True,mode=0o700,exist_ok=True)
    binding = '|'.join(config['target'][k] for k in ['origin','org_id','user_id'])
    lease = (lock_dir/(hashlib.sha256(binding.encode()).hexdigest()+'.lock')).open('a')
    fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB) # Held through cleanup.
    output = base/('run-'+uuid.uuid4().hex)
    output.mkdir(parents=True, mode=0o700)
    config.update(output=str(output), run_id=output.name)
    session = output/'session.json'
    session.write_text(json.dumps(config, indent=2))
    os.environ['OH_LOAD_SESSION'] = str(session)
    import api
    from sandbox import Sandbox
    from state import Ledger, atomic_json
    from cleanup import clean
    state = {'phase':'preflight','waves':[],'stop_reason':None}
    lock = threading.RLock()
    done = threading.Event()
    pool = []
    health = {'failures':0}
    deadline = time.monotonic()+config['max_run_seconds']
    script = (ROOT/'harness/job.py').read_text()
    digest = hashlib.sha256(script.encode()).hexdigest()
    catalog = json.loads((ROOT/'workloads.json').read_text())['workloads']

    def save():
        with lock:
            atomic_json(output/'status.json',state)

    def stop(reason):
        with lock:
            state['stop_reason'] = state['stop_reason'] or reason
        api.STOP_EVENT.set()
        save()

    def check_budget():
        if time.monotonic() >= deadline:
            stop('run_deadline')
        api.check_stop()

    def observer():
        while not done.is_set():
            tick = time.monotonic()
            try:
                status, _, seconds = api.api('GET','/alive',timeout=2, throttle=False)
            except Exception:
                status, seconds = None, None
            row = {'time':time.time(),'status':status,'seconds':seconds}
            with (output/'health.jsonl').open('a') as f:
                f.write(json.dumps(row)+'\n')
            with lock:
                health['failures'] = 0 if status == 200 else health['failures']+1
                state['alive_status'] = status
                state['consecutive_health_failures'] = health['failures']
            save()
            if health['failures'] >= config['health_failure_limit']:
                stop('application_unavailable')
                break
            if time.monotonic() >= deadline:
                stop('run_deadline')
                break
            done.wait(max(0,2-(time.monotonic()-tick)))

    def keep_warm():
        # Keep early prepared runtimes from idling out during a large cohort.
        while not done.is_set() and not api.STOP_EVENT.is_set():
            for item in list(pool):
                if done.is_set() or api.STOP_EVENT.is_set():
                    return
                try:
                    item['runtime'].call('GET',item['route'],timeout=3)
                except Exception:
                    pass # The measured worker/startup path records failures.
            if api.STOP_EVENT.wait(30):
                return

    def prepare(index):
        check_budget()
        kind = ['interactive','build','automation'][index%3]
        row = api.sandbox()
        runtime = Sandbox(row, config['target']['sandbox_origins'], stop_event=api.STOP_EVENT)
        root = '/tmp/oh-concurrency/'+config['run_id']+'/job-'+str(index)
        program = ('import pathlib,shutil,subprocess;root=pathlib.Path('+repr(root)+');'
                   'assert not root.exists();root.mkdir(parents=True);'
                   'shutil.copytree('+repr('/opt/load-fixtures/'+kind+'/repo')+',root/"repo",symlinks=True);'
                   'assert subprocess.check_output(["git","rev-parse","HEAD"],cwd=root/"repo",text=True).strip()=='+repr(catalog[kind]['commit'])+';'
                   '(root/"job.py").write_text('+repr(script)+')')
        command = 'python -c '+shlex.quote('import base64;exec(base64.b64decode('+repr(base64.b64encode(program.encode()).decode())+'))')
        result = runtime.execute(command,timeout=180)
        if result['exit_code'] != 0:
            raise RuntimeError('Fixture preparation failed')
        check_budget()
        cid = api.conversation(row['id'],None,'Prepared script '+str(index),run=False)
        route = '/api/conversations/'+cid
        native_state = runtime.call('GET',route)
        if native_state.get('execution_status',native_state.get('state',{}).get('execution_status')) not in ['idle','finished']:
            raise RuntimeError('New native conversation is not idle')
        return {'index':index,'kind':kind,'runtime':runtime,'root':root,'route':route}

    def job(item, level, gate, start):
        result = {'index':item['index'],'kind':item['kind'],'passed':False,'outcome':'unverified'}
        try:
            gate.wait()
            check_budget()
            runtime, route = item['runtime'], item['route']
            runtime.call('POST',route+'/run',timeout=15)
            limit = min(deadline,time.monotonic()+config['wave_timeout_seconds'])
            last_check = 0
            while time.monotonic() < limit:
                check_budget()
                v = runtime.call('GET',route,timeout=15)
                status = v.get('execution_status',v.get('state',{}).get('execution_status'))
                with lock:
                    native[item['index']] = (status,time.monotonic())
                    state['peak_native_running'] = max(state['peak_native_running'],sum(s=='running' and time.monotonic()-t <= 15 for s,t in native.values()))
                if status in ['finished','error','stuck','paused','waiting_for_confirmation']:
                    break
                if time.time()-start >= 65 and time.monotonic()-last_check >= 20:
                    last_check = time.monotonic()
                    code = 'import pathlib;print((pathlib.Path('+repr(item['root'])+')/'+repr('result-'+str(level)+'.json')+').exists())'
                    found = runtime.execute('python -c '+shlex.quote(code),timeout=15)
                    if found['exit_code']==0 and found['stdout'].strip()=='True':
                        break
                if api.STOP_EVENT.wait(5):
                    check_budget()
            else:
                raise TimeoutError('Job observation deadline')
            code = ('import pathlib,hashlib,subprocess;p=pathlib.Path('+repr(item['root'])+');'
                    'assert hashlib.sha256((p/"job.py").read_bytes()).hexdigest()=='+repr(digest)+';'
                    'assert subprocess.run(["git","diff","--quiet","HEAD"],cwd=p/"repo").returncode==0;'
                    'print((p/'+repr('result-'+str(level)+'.json')+').read_text())')
            verified = runtime.execute('python -c '+shlex.quote(code),timeout=30)
            if verified['exit_code'] != 0:
                raise RuntimeError('Independent result verification failed')
            measured = json.loads(verified['stdout'])
            if measured['wave'] != level or measured['kind'] != item['kind'] or measured['started'] < start-5:
                raise ValueError('Result does not match this wave')
            result.update(measured,outcome='passed' if measured['passed'] else 'script_failed')
        except Exception as exc:
            result['error'] = type(exc).__name__
            if api.STOP_EVENT.is_set():
                result['outcome']='interrupted_unverified'
        with lock:
            rows.append(result)
            atomic_json(output/('wave-'+str(level)+'-partial.json'),rows)
            state['completed_now'] = len(rows)
            save()
        return result

    signal.signal(signal.SIGINT,lambda *_:stop('operator_stop'))
    signal.signal(signal.SIGTERM,lambda *_:stop('operator_stop'))
    initialized = False
    observer_thread = None
    warm_thread = None
    failed = False
    cleanup_ok = False
    try:
        key = api.checked('GET','/api/keys/current')
        if key['bound_org_id'] != api.C['expected_org_id'] or key['user_id'] != api.C['expected_user_id']:
            raise ValueError('Credential principal or organization mismatch')
        inventory = api.inventory()
        if any(x['status'] in ['RUNNING','STARTING'] for x in inventory):
            raise ValueError('Use an idle dedicated fixture')
        settings = api.checked('GET','/api/v1/settings')
        if settings['sandbox_grouping_strategy'] != 'NO_GROUPING':
            raise ValueError('Configure NO_GROUPING before running')
        Ledger(output).initialize({'sandboxes':[x['id'] for x in inventory]},config['run_id'])
        initialized = True
        observer_thread = threading.Thread(target=observer,daemon=True)
        observer_thread.start()
        warm_thread = threading.Thread(target=keep_warm,daemon=True)
        warm_thread.start()
        for level in config['levels']:
            check_budget()
            state.update(phase='preparing',current_jobs=level)
            save()
            # Each level uses a new cohort. Completed cohorts are removed below.
            pool=[]
            with concurrent.futures.ThreadPoolExecutor(max_workers=config['prepare_workers']) as ex:
                futures=[ex.submit(prepare,i) for i in range(level)]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        pool.append(future.result())
                    except Exception:
                        stop('startup_or_admission_failure')
                        raise
            check_budget()
            for item in pool:
                command = 'SIMPLE_COMMAND_TIMEOUT=900 python '+item['root']+'/job.py '+item['root']+' '+item['kind']+' '+str(level)
                prompt = 'Run exactly this prepared command with a bash timeout of 1000 seconds: '+command+'. Do not edit files, install packages or rerun it. Wait for completion and report its exit status only.'
                item['runtime'].call('POST',item['route']+'/events',{'role':'user','content':[{'type':'text','text':prompt}],'run':False})
            rows=[]
            native={}
            gate=threading.Barrier(level+1)
            state.update(phase='running',peak_native_running=0,completed_now=0)
            save()
            start=time.time()
            with concurrent.futures.ThreadPoolExecutor(max_workers=level) as ex:
                futures=[ex.submit(job,item,level,gate,start) for item in pool]
                gate.wait()
                for f in concurrent.futures.as_completed(futures):
                    f.result()
            passed=sum(bool(r.get('passed')) for r in rows)
            record={'jobs':level,'passed':passed,'unverified_or_failed':level-passed,
                    'observed_native_peak':state['peak_native_running'],'verified_script_overlap_peak':overlap(rows),
                    'seconds':round(time.time()-start,1)}
            atomic_json(output/('wave-'+str(level)+'.json'),rows)
            state['waves'].append(record)
            save()
            if level-passed > config.get('allowed_unsuccessful',0):
                stop('unsuccessful_workloads')
            check_budget()
            # End this cohort before the next one; the ledger retains provenance.
            cleanup = clean(output,api)
            if not cleanup['completed']:
                stop('cleanup_incomplete')
                raise RuntimeError('Cleanup incomplete; do not start another cohort')
    except Exception as exc:
        failed = True
        state['driver_error_class'] = type(exc).__name__
        state['stop_reason'] = state['stop_reason'] or 'preflight_or_driver_error'
    finally:
        done.set()
        api.STOP_EVENT.set()
        if observer_thread:
            observer_thread.join(timeout=5)
        if warm_thread:
            warm_thread.join(timeout=5)
        state['phase']='cleanup'
        save()
        if initialized:
            try:
                cleanup = clean(output,api)
                cleanup_ok=cleanup['completed']
            except Exception as exc:
                state['cleanup_error_class']=type(exc).__name__
        else:
            cleanup_ok=True # No creations can precede ledger initialization.
        state.update(phase='finished',cleanup_verified=cleanup_ok)
        save()
        lease.close()
    print(json.dumps({'run_directory':str(output),'waves':state['waves'],'stop_reason':state['stop_reason'],'cleanup_verified':cleanup_ok}))
    return 1 if failed or state['stop_reason'] or not cleanup_ok else 0


if __name__=='__main__':
    raise SystemExit(main())
