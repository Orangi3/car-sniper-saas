"""Only the disposable CI PostgreSQL fixture is reachable during tests."""
import os,socket,sys,json
from pathlib import Path
root=Path.cwd()
sys.path.insert(0,str(root))
for key in list(os.environ):
    if key in ('DATABASE_URL','SNIPER_TEST_PG_URL') or key.startswith(('SMTP_','STRIPE_','TWILIO_','EBAY_','MARKETCHECK_','NTFY_')) or key.endswith(('_TOKEN','_SECRET','_API_KEY')):
        os.environ.pop(key,None)
os.environ['SNIPER_TEST_PG_URL']='postgresql://fixture_admin:ephemeral-fixture-only@127.0.0.1:55441/postgres'
os.environ['SNIPER_DB_PATH']=str(root/'unused-fixture.sqlite')
connect=socket.socket.connect
connect_ex=socket.socket.connect_ex
def allowed(address):
    return isinstance(address,tuple) and address[0]=='127.0.0.1' and address[1]==55441
def guarded(self,address):
    if not allowed(address):raise RuntimeError('External network blocked for synthetic CI tests')
    return connect(self,address)
def guarded_ex(self,address):
    if not allowed(address):raise RuntimeError('External network blocked for synthetic CI tests')
    return connect_ex(self,address)
socket.socket.connect=guarded
socket.socket.connect_ex=guarded_ex
import psycopg2
with psycopg2.connect(os.environ['SNIPER_TEST_PG_URL']) as c:
    with c.cursor() as cur:
        cur.execute('SELECT version()');print(json.dumps({'postgres_version':cur.fetchone()[0]}))
import pytest
raise SystemExit(pytest.main(sys.argv[1:]))
