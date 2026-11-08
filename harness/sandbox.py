"""Portable Agent Server bash API; no Docker/Kubernetes dependency for task verification."""
import http.client
import json
import re
import time
import urllib.parse


class RuntimeAPIError(RuntimeError):
    def __init__(self,status):
        self.status=status
        super().__init__('Runtime API request failed: '+str(status))


class Sandbox:
    def __init__(self, row, allowed_origins, stop_event=None):
        urls=[x['url'] for x in row.get('exposed_urls',[]) if x['name']=='AGENT_SERVER']
        if len(urls)!=1 or not row.get('session_api_key'):raise ValueError('Sandbox runtime endpoint unavailable')
        self.url=urllib.parse.urlsplit(urls[0]);origin=urllib.parse.urlunsplit((self.url.scheme,self.url.netloc,'','',''))
        allowed = origin in allowed_origins
        for pattern in allowed_origins:
            if pattern.startswith('https://*.'):
                suffix=pattern[len('https://*.'):]
                hostname=self.url.hostname or ''
                if re.fullmatch(r'[a-z0-9.-]+',suffix) and hostname.endswith('.'+suffix) and self.url.port in [None,443]:
                    prefix=hostname[:-(len(suffix)+1)]
                    allowed = allowed or bool(prefix and '.' not in prefix)
        if self.url.scheme!='https' or not allowed or self.url.query or self.url.fragment or self.url.username:
            raise ValueError('Unapproved runtime origin')
        self.key=row['session_api_key']; self.stop_event=stop_event

    def call(self,method,path,body=None,timeout=30):
        if self.stop_event is not None and self.stop_event.is_set(): raise InterruptedError('Run stopping')
        conn=http.client.HTTPSConnection(self.url.hostname,self.url.port or 443,timeout=timeout)
        try:
            conn.request(method,self.url.path.rstrip('/')+path,
                         body=None if body is None else json.dumps(body),
                         headers={'X-Session-API-Key':self.key,'Content-Type':'application/json'})
            r=conn.getresponse();raw=r.read()
            if r.status>=300:raise RuntimeAPIError(r.status)
            return json.loads(raw) if raw else None
        finally:conn.close()

    def execute(self,command,cwd=None,timeout=600):
        # Server deadline bounds the command even if the controller loses connectivity.
        task=self.call('POST','/api/bash/start_bash_command',{'command':command,'cwd':cwd,'timeout':timeout})
        deadline=time.monotonic()+timeout+15;chunks={};page=None
        while time.monotonic()<deadline:
            query={'command_id__eq':task['id'],'kind__eq':'BashOutput','sort_order':'TIMESTAMP','limit':100}
            if page:query['page_id']=page
            d=self.call('GET','/api/bash/bash_events/search?'+urllib.parse.urlencode(query))
            for r in d.get('items',[]):chunks[r['id']]=r
            if sum(len(x.get('stdout') or '')+len(x.get('stderr') or '') for x in chunks.values())>5_000_000:
                raise RuntimeError('Verification output exceeds bounded collection size')
            page=d.get('next_page_id')
            if page:continue
            rows=sorted(chunks.values(),key=lambda x:x['order'])
            if rows and rows[-1].get('exit_code') is not None:
                return {'exit_code':rows[-1]['exit_code'],'stdout':''.join(x.get('stdout') or '' for x in rows),
                        'stderr':''.join(x.get('stderr') or '' for x in rows)}
            time.sleep(1)
        raise TimeoutError('Runtime command deadline; preserve as uncertain, never retry automatically')
