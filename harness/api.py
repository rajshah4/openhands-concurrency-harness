"""Version-scoped application API. Never logs credentials or response bodies."""
import fcntl
import http.client
import json
import os
import pathlib
import time
import urllib.parse
from state import Ledger, atomic_json

SESSION = json.loads(pathlib.Path(os.environ['OH_LOAD_SESSION']).read_text()) if os.environ.get('OH_LOAD_SESSION') else {}
P = pathlib.Path(SESSION['output']) if SESSION else None
C = ({'origin':SESSION['target']['origin'], 'expected_org_id':SESSION['target']['org_id'],
      'expected_user_id':SESSION['target']['user_id'],
      'api_key':os.environ[SESSION.get('api_key_env','OPENHANDS_API_KEY')]} if SESSION else {})
ORIGIN = urllib.parse.urlsplit(C.get('origin','https://invalid.example'))
CREATE_PATHS = {'/api/v1/sandboxes', '/api/v1/app-conversations'}
import threading
STOP_EVENT = threading.Event()

def check_stop():
    if STOP_EVENT.is_set(): raise InterruptedError('Run stopping')


def event(kind, **data):
    row = {'time':time.time(), 'kind':kind, **data}
    with (P/'events.jsonl').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX); f.write(json.dumps(row)+'\n'); f.flush()


def creation_slot():
    # Cross-process throttle applies to resource creation only, never GET/poll/probes.
    with (P/'creation.lock').open('a+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0); last = float(f.read() or 0)
        delay = max(0, last+SESSION.get('create_interval_seconds',.35)-time.time())
        if delay: time.sleep(delay)
        f.seek(0); f.truncate(); f.write(str(time.time())); f.flush()


def request_slot():
    """Optional shared control-plane budget; resident observers use the same lock."""
    rate=SESSION.get('maximum_control_requests_per_second',.5 if SESSION.get('factory_mode') else None)
    if rate is None:return
    if not isinstance(rate,(int,float)) or not .5<=rate<=3:raise ValueError('Invalid control request budget')
    with (P/'request.lock').open('a+') as f:
        fcntl.flock(f,fcntl.LOCK_EX);f.seek(0);last=float(f.read() or 0)
        delay=max(0,last+1/rate-time.time())
        if delay:time.sleep(delay)
        f.seek(0);f.truncate();f.write(str(time.time()));f.flush()


def api(method, path, body=None, timeout=45, throttle=True):
    intended = time.time()
    if method == 'POST' and path in CREATE_PATHS: creation_slot()
    if throttle: request_slot()
    conn = http.client.HTTPSConnection(ORIGIN.hostname, ORIGIN.port or 443, timeout=timeout)
    sent = time.time(); tick = time.monotonic(); status = None; failure = None
    try:
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={'Authorization':'Bearer '+C['api_key'], 'X-Org-Id':C['expected_org_id'], 'Content-Type':'application/json'})
        r = conn.getresponse(); raw = r.read(); status = r.status
        try: data = json.loads(raw)
        except (ValueError, UnicodeDecodeError): data = {'non_json_bytes':len(raw)}
        return status, data, time.monotonic()-tick
    except Exception as e:
        failure = type(e).__name__
        e.request_seconds=time.monotonic()-tick;e.client_queue_seconds=sent-intended
        raise
    finally:
        conn.close()
        event('http', method=method, path=path.split('?')[0], intended=intended, sent=sent,
              client_queue_seconds=sent-intended, seconds=time.monotonic()-tick, status=status, error=failure)


def checked(method,path,body=None,timeout=45):
    status,data,_ = api(method,path,body,timeout)
    if status >= 300: raise RuntimeError('Application HTTP '+str(status)+'; details private')
    return data


def items(data):
    return data.get('items',[]) if isinstance(data,dict) else data


def inventory():
    rows=[]; page=None; seen=set()
    while True:
        path='/api/v1/sandboxes/search'+('?page_id='+urllib.parse.quote(page,safe='') if page else '')
        d=checked('GET',path); rows.extend(x for x in items(d) if x)
        page=d.get('next_page_id') if isinstance(d,dict) else None
        if not page: return rows
        if page in seen: raise RuntimeError('Repeated inventory page')
        seen.add(page)


def owner(row, sandbox=False):
    if row.get('created_by_user_id') != C['expected_user_id']: raise ValueError('Resource principal mismatch')
    if sandbox and str(row.get('credential_org_id')) != C['expected_org_id']:
        legacy_personal = (SESSION.get('target',{}).get('legacy_personal_sandbox_schema') is True
                           and C['expected_org_id'] == C['expected_user_id']
                           and 'credential_org_id' not in row)
        if not legacy_personal: raise ValueError('Resource org mismatch')
    return row


def sandbox_row(sid):
    rows=items(checked('GET','/api/v1/sandboxes?id='+urllib.parse.quote(sid,safe='')))
    return next((x for x in rows if x and x['id']==sid),None)


def sandbox():
    check_stop(); ledger=Ledger(P); intent=ledger.intent('sandboxes','explicit-create')
    t=time.monotonic(); s=checked('POST','/api/v1/sandboxes',timeout=120)
    atomic_json(P/('sandbox-create-response-'+intent+'.json'),s)
    owner(s,True); ledger.claim('sandboxes',s,intent); sid=s['id']
    while s['status']=='STARTING' and time.monotonic()-t < 180:
        check_stop(); time.sleep(2); s=sandbox_row(sid)
        if not s: raise RuntimeError('Created sandbox disappeared')
    if s['status']!='RUNNING': raise RuntimeError('Sandbox not running')
    event('sandbox-ready',sandbox_id=sid,seconds=time.monotonic()-t)
    return owner(s,True)


def conversation(sid,prompt,title,run=True):
    check_stop(); ledger=Ledger(P); intent=ledger.intent('conversations',title)
    body={'sandbox_id':sid,'title':title,'generate_title_on_submit':False,
          'llm_profile':SESSION['model_profile'],'tags':{'loadtest':SESSION['run_id']}}
    if prompt is not None:body['initial_message']={'content':[{'type':'text','text':prompt}],'run':run}
    t=time.monotonic();v=checked('POST','/api/v1/app-conversations',body)
    tid=v['id']
    ledger.update(lambda d:d['intents'][intent].update(start_task_id=tid))
    while v['status'] not in ['READY','ERROR'] and time.monotonic()-t < 180:
        check_stop(); time.sleep(2); rows=items(checked('GET','/api/v1/app-conversations/start-tasks?ids='+tid)); v=rows[0]
    if v['status']!='READY': raise RuntimeError('Conversation startup did not reach READY')
    cid=v['app_conversation_id']; row=items(checked('GET','/api/v1/app-conversations?ids='+cid))[0]
    owner(row); ledger.claim('conversations',row,intent)
    event('conversation-ready',conversation_id=cid,sandbox_id=sid,seconds=time.monotonic()-t)
    return cid
