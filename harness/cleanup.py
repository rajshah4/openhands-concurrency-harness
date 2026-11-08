"""Delete only proved new resources; keep going and verify postconditions."""
import json
import time
from pathlib import Path
from state import Ledger, atomic_json


def clean(folder, service):
    folder=Path(folder); ledger=Ledger(folder); errors=[]; receipts=[]
    (folder/'fixtures-cleaned').unlink(missing_ok=True)
    def attempt(label, fn):
        try: return fn()
        except Exception as e:
            errors.append({'operation':label,'error':type(e).__name__}); return None
    # Reconcile responses to asynchronous creation; never adopt arbitrary inventory additions.
    for intent, request in ledger.read()['intents'].items():
        if request.get('resolved') or not request.get('start_task_id'): continue
        def reconcile():
            rows=service.items(service.checked('GET','/api/v1/app-conversations/start-tasks?ids='+request['start_task_id']))
            task=rows[0] if rows else None
            if task and task.get('status')=='READY':
                cid=task['app_conversation_id']; row=service.items(service.checked('GET','/api/v1/app-conversations?ids='+cid))[0]
                service.owner(row); ledger.claim('conversations',row,intent)
            elif task and task.get('status')=='ERROR':
                # Error alone does not establish that no partial resources exist.
                raise RuntimeError('Failed start requires resource reconciliation')
            else: raise RuntimeError('Creation is still unresolved')
        attempt('reconcile-start',reconcile)
    for kind in ['conversations','sandboxes']:
        for ident in ledger.read().get(kind,[]):
            def remove():
                if not ledger.deletable(kind,ident): raise ValueError('Missing creation provenance')
                paths={'conversations':'/api/v1/app-conversations/','sandboxes':'/api/v1/sandboxes/'}
                status,_,_=service.api('DELETE',paths[kind]+ident,timeout=30)
                if status not in [200,204,404]: raise RuntimeError('Deletion rejected')
                # Route validity is qualified before any run; prove disappearance independently.
                gone=False
                for _ in range(10):
                    if kind=='sandboxes': gone=ident not in {x['id'] for x in service.inventory()}
                    elif kind=='conversations':
                        rows=service.items(service.checked('GET','/api/v1/app-conversations?ids='+ident))
                        gone=not any(x and x.get('id')==ident for x in rows)
                    else: gone=service.api('GET',paths[kind]+ident)[0]==404
                    if gone: break
                    time.sleep(1)
                if not gone: raise RuntimeError('Resource still visible after delete')
                receipts.append({'kind':kind,'id':ident,'verified_absent':True})
            attempt('delete-'+kind,remove)
    unresolved=ledger.unresolved()
    result={'completed':not errors and not unresolved,'errors':errors,'unresolved_intents':unresolved,'receipts':receipts,'time':time.time()}
    atomic_json(folder/'cleanup.json',result)
    if result['completed']: (folder/'fixtures-cleaned').write_text(str(time.time()))
    return result


if __name__=='__main__':
    import api
    identity=api.checked('GET','/api/keys/current')
    if identity['bound_org_id']!=api.C['expected_org_id'] or identity['user_id']!=api.C['expected_user_id']:
        raise ValueError('Cleanup credential binding mismatch')
    outcome=clean(api.P,api)
    if not outcome['completed']: raise SystemExit(2)
