"""One bounded, repeatable script job. Runs inside the remote test runtime only."""
import json,os,pathlib,subprocess,sys,time,traceback
root=pathlib.Path(sys.argv[1]);kind=sys.argv[2];wave=int(sys.argv[3]);repo=root/'repo';out=root/('result-'+str(wave)+'.json');assert kind in ['interactive','build','automation'] and not out.exists()
started=time.time();cycles=0;error=None
(root/('started-'+str(wave)+'.json')).write_text(json.dumps({'kind':kind,'wave':wave,'started':started}))
commands={
 'interactive':[['/opt/load-fixtures/interactive/venv/bin/python','-m','pytest','-q']],
 'automation':[['/opt/load-fixtures/automation/venv/bin/python','-m','pytest','tests/test_basic.py','-q']],
 'build':[['/usr/local/cargo/bin/cargo','+1.82.0','clean'],['/usr/local/cargo/bin/cargo','+1.82.0','build','--release','--locked','--offline','--jobs','2'],['/usr/local/cargo/bin/cargo','+1.82.0','test','--workspace','--locked','--offline','--jobs','2']]}
env=dict(os.environ,PYTHONPATH=str(repo/'src'),PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONDONTWRITEBYTECODE='1',PATH='/usr/local/cargo/bin:'+os.environ.get('PATH',''))
try:
 while time.time()-started<60:
  for command in commands[kind]:
   with (root/('commands-'+str(wave)+'.log')).open('ab') as log:
    r=subprocess.run(command,cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=int(os.environ.get('SIMPLE_COMMAND_TIMEOUT','300')))
   if r.returncode:raise RuntimeError('Command exit '+str(r.returncode))
  cycles+=1
  print(json.dumps({'wave':wave,'kind':kind,'cycles':cycles,'elapsed':round(time.time()-started,2)}),flush=True)
except Exception as exc:error=type(exc).__name__
result={'kind':kind,'wave':wave,'started':started,'finished':time.time(),'cycles':cycles,'passed':error is None and cycles>0,'error':error};out.write_text(json.dumps(result));print(json.dumps(result),flush=True);sys.exit(0 if result['passed'] else 1)
