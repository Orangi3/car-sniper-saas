import os,sys,socket
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
def deny(*args,**kwargs):raise RuntimeError('External transport forbidden by provider-budget verification')
socket.socket.connect=deny
socket.socket.connect_ex=deny
import pytest
raise SystemExit(pytest.main(sys.argv[1:]))
