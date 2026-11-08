"""Image-build provisioning only. Sources are pinned in workloads.json."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def call(args,cwd=None):subprocess.run(args,cwd=cwd,check=True)


def main():
    if sys.version_info[:2]!=(3,13):raise RuntimeError('Fixture image requires Python 3.13')
    root=Path('/opt/load-fixtures');manifest=json.loads(Path('/tmp/load-kit/workloads.json').read_text())
    if root.exists():raise RuntimeError('Do not overwrite a pre-existing fixture directory')
    root.mkdir()
    for name,spec in manifest['workloads'].items():
        directory=root/name;directory.mkdir();repo=directory/'repo';repo.mkdir()
        call(['git','init',str(repo)]);call(['git','remote','add','origin',spec['repository']],repo)
        call(['git','fetch','--depth','1','origin',spec['commit']],repo);call(['git','checkout','--detach','FETCH_HEAD'],repo)
        if name=='build':
            call(['cargo','+1.82.0','fetch','--locked'],repo)
        else:
            for relative,expected in [('requirements/tests.txt',spec['test_lock_sha256'])]:
                if hashlib.sha256((repo/relative).read_bytes()).hexdigest()!=expected:raise RuntimeError('Upstream fixture lock mismatch')
            call([sys.executable,'-m','venv','--without-pip',str(directory/'venv')])
            pip=[sys.executable,'-m','pip','--python',str(directory/'venv/bin/python')]
            call(pip+['install','--no-deps','-r','/tmp/load-kit/fixtures/python-runtime-requirements.txt','-r',str(repo/'requirements/tests.txt')])
            (directory/'installed.txt').write_text(subprocess.check_output(pip+['freeze','--all'],text=True))
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    Path('/tmp/load-kit/runtime-packages.txt').write_text(subprocess.check_output(['dpkg-query','-W'],text=True))


if __name__=='__main__':main()
