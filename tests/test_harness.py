import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'harness'))
import run as runner
from state import Ledger
from sandbox import Sandbox


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/'config.example.json').read_text())

    def test_dry_run_needs_no_credential_or_network(self):
        with patch.object(sys,'argv',['run','--config',str(ROOT/'config.example.json')]), contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(runner.main(),0)
        self.assertIn('No network',stdout.getvalue())

    def test_invalid_targets_and_levels_fail_before_execution(self):
        for origin in ['http://app.example.com','https://user:pass@app.example.com','https://app.example.com/path','https://app.example.com?key=value']:
            c=copy.deepcopy(self.config);c['target']['origin']=origin
            with self.assertRaises(ValueError):runner.validate(c)
        for levels in [[0],[4,1],[1,1],[]]:
            c=copy.deepcopy(self.config);c['levels']=levels
            with self.assertRaises(ValueError):runner.validate(c)

    def test_successful_overlap_excludes_unknown_and_touching_intervals(self):
        rows=[{'passed':True,'started':1,'finished':4},{'passed':True,'started':2,'finished':3},
              {'passed':False,'started':1,'finished':5},{'passed':True,'started':4,'finished':6}]
        self.assertEqual(runner.overlap(rows),2)


class OwnershipTests(unittest.TestCase):
    def test_baseline_and_unproven_resources_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=Ledger(d);ledger.initialize({'sandboxes':['existing']},'test')
            intent=ledger.intent('sandboxes','test create')
            with self.assertRaises(ValueError):ledger.claim('sandboxes',{'id':'existing'},intent)
            self.assertFalse(ledger.deletable('sandboxes','unknown'))
            ledger.claim('sandboxes',{'id':'new'},intent)
            self.assertTrue(ledger.deletable('sandboxes','new'))
            self.assertFalse(ledger.deletable('sandboxes','existing'))

    def test_runtime_credentials_cannot_go_to_unapproved_origin(self):
        for url in ['https://other.example.net','http://runtime.example.com','https://a.b.runtimes.example.com','https://a.runtimes.example.com/?token=example']:
            row={'exposed_urls':[{'name':'AGENT_SERVER','url':url}],'session_api_key':'synthetic-test-only'}
            with self.assertRaises(ValueError):Sandbox(row,['https://*.runtimes.example.com'])


class LifecycleTests(unittest.TestCase):
    def test_application_unavailable_stops_and_runs_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            c=json.loads((ROOT/'config.example.json').read_text());c.update(levels=[1],health_failure_limit=1)
            config=Path(d)/'config.json';config.write_text(json.dumps(c))
            fake=types.ModuleType('api');fake.STOP_EVENT=threading.Event()
            fake.C={'expected_org_id':c['target']['org_id'],'expected_user_id':c['target']['user_id']}
            def checked(method,path,*args,**kwargs):
                if path=='/api/keys/current':return {'bound_org_id':fake.C['expected_org_id'],'user_id':fake.C['expected_user_id']}
                if path=='/api/v1/settings':return {'sandbox_grouping_strategy':'NO_GROUPING'}
                raise AssertionError('Unexpected route')
            fake.checked=checked;fake.inventory=lambda:[]
            fake.api=lambda *a,**kw:(503,{},0.01)
            def check_stop():
                if fake.STOP_EVENT.is_set():raise InterruptedError('Run stopping')
            fake.check_stop=check_stop
            def sandbox():
                fake.STOP_EVENT.wait(2);check_stop();raise AssertionError('Health stop was not propagated')
            fake.sandbox=sandbox
            cleanup=types.ModuleType('cleanup');calls=[]
            def clean(folder,service):
                self.assertTrue((folder/'owned.json').exists())
                calls.append(folder)
                return {'completed':True}
            cleanup.clean=clean
            with patch.dict(sys.modules,{'api':fake,'cleanup':cleanup}), patch.object(sys,'argv',['run','--config',str(config),'--output',str(Path(d)/'runs'),'--execute']), patch.dict(os.environ,{},clear=False), patch.object(runner.signal,'signal'),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(),1)
            self.assertEqual(len(calls),1)
            status=json.loads((calls[0]/'status.json').read_text())
            self.assertEqual(status['stop_reason'],'application_unavailable')
            self.assertTrue(status['cleanup_verified'])
            self.assertEqual(status['phase'],'finished')

    def test_two_cohorts_collect_verified_records_and_cleanup_between_levels(self):
        import re
        import time
        with tempfile.TemporaryDirectory() as d:
            c=json.loads((ROOT/'config.example.json').read_text());c.update(levels=[1,2])
            config=Path(d)/'config.json';config.write_text(json.dumps(c))
            fake=types.ModuleType('api');fake.STOP_EVENT=threading.Event()
            fake.C={'expected_org_id':c['target']['org_id'],'expected_user_id':c['target']['user_id']}
            fake.checked=lambda method,path,*a,**kw: ({'bound_org_id':fake.C['expected_org_id'],'user_id':fake.C['expected_user_id']} if path=='/api/keys/current' else {'sandbox_grouping_strategy':'NO_GROUPING'})
            fake.inventory=lambda:[];fake.api=lambda *a,**kw:(200,{},0.01)
            fake.check_stop=lambda:None
            counter=iter(range(3));fake.sandbox=lambda:{'id':str(next(counter))}
            fake.conversation=lambda sid,*a,**kw:sid
            runtime_module=types.ModuleType('sandbox')
            class FakeSandbox:
                def __init__(self,row,*a,**kw):self.running=False;self.kind=None
                def call(self,method,path,body=None,**kw):
                    if path.endswith('/events'):
                        text=body['content'][0]['text'];m=re.search(r' (interactive|build|automation) ([0-9]+)\.',text)
                        self.kind=m.group(1);self.wave=int(m.group(2));return {}
                    if path.endswith('/run'):self.running=True;self.started=time.time();return {}
                    return {'execution_status':'finished' if self.running else 'idle'}
                def execute(self,command,**kw):
                    value={'passed':True,'kind':self.kind,'wave':getattr(self,'wave',0),'started':getattr(self,'started',0),'finished':time.time(),'cycles':1}
                    return {'exit_code':0,'stdout':json.dumps(value),'stderr':''}
            runtime_module.Sandbox=FakeSandbox
            cleanup=types.ModuleType('cleanup');phases=[]
            def clean(folder,service):
                phases.append(json.loads((folder/'status.json').read_text())['phase']);return {'completed':True}
            cleanup.clean=clean
            with patch.dict(sys.modules,{'api':fake,'sandbox':runtime_module,'cleanup':cleanup}),patch.object(sys,'argv',['run','--config',str(config),'--output',str(Path(d)/'runs'),'--execute']),patch.object(runner.signal,'signal'),contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(runner.main(),0)
            summary=json.loads(stdout.getvalue())
            self.assertEqual([x['passed'] for x in summary['waves']],[1,2])
            self.assertEqual(len(phases),3)
            self.assertEqual(phases[-1],'cleanup')
            for level in [1,2]:
                rows=json.loads((Path(summary['run_directory'])/('wave-'+str(level)+'.json')).read_text())
                self.assertEqual(len(rows),level)
                self.assertTrue(all(x['outcome']=='passed' for x in rows))

    def test_subprocess_timeout_records_failure_instead_of_pass(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'repo').mkdir()
            with patch.object(sys,'argv',['job.py',d,'build','1']),patch('subprocess.run',side_effect=subprocess.TimeoutExpired('synthetic-command',1)),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:runpy.run_path(str(ROOT/'harness/job.py'),run_name='__main__')
            self.assertEqual(exc.exception.code,1)
            result=json.loads((root/'result-1.json').read_text())
            self.assertFalse(result['passed']);self.assertEqual(result['error'],'TimeoutExpired')


if __name__=='__main__':unittest.main()
