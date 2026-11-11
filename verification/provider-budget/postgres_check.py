"""NOT EXECUTED HERE. Requires a disposable localhost PostgreSQL database.
Run from an exact candidate checkout. Requires a fresh dedicated database; refuses an existing usage_events table.
FIXTURE_PG_DSN must target a database named provider_budget_fixture.
"""
import os,sys,json,time,multiprocessing as mp
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0,str(Path.cwd()))
def worker(dsn, source, cap, gate, crash=False):
    import db,provider_budget as pb
    db.IS_PG=True;db.DATABASE_URL=dsn
    os.environ[source.upper()+'_MONTHLY_CALL_BUDGET']=str(cap)
    gate.wait(30)
    t=time.monotonic();ok=pb.reserve(source)
    if crash and ok:os._exit(23)
    return [source,int(ok),int(ok),time.monotonic()-t]

def main():
    import psycopg2,db,provider_budget as pb
    from unittest.mock import patch
    from datetime import datetime,timezone
    from urllib.parse import urlparse
    import uuid
    dsn=os.environ['FIXTURE_PG_DSN']
    u=urlparse(dsn)
    assert u.hostname in ('localhost','127.0.0.1') and u.path=='/provider_budget_fixture'
    report={'tests':[]}
    check=psycopg2.connect(dsn)
    try:
        cur=check.cursor();cur.execute("SELECT to_regclass('public.usage_events')")
        assert cur.fetchone()[0] is None, 'Database is not empty: refusing reuse'
    finally:check.close()
    try:
        db.IS_PG=True;db.DATABASE_URL=dsn
        with db.session() as c:
            report['version']=c.execute('SELECT version()').fetchone()[0]
            c.execute('CREATE TABLE usage_events(id BIGSERIAL PRIMARY KEY,user_id BIGINT,kind TEXT,units INTEGER,cost_cents DOUBLE PRECISION,created_at TIMESTAMPTZ)')
        def clear():
            with db.session() as c:c.execute('TRUNCATE usage_events')
        ctx=mp.get_context('spawn')
        with ctx.Manager() as manager:
            for sources,cap in [(['marketcheck'],1),(['marketcheck']*2,1),(['marketcheck']*8,3),(['marketcheck']*24,5),(['marketcheck']*4+['marketcheck_fsbo']*4,2)]:
                clear();gate=manager.Barrier(len(sources));batch_start=time.monotonic()
                with ProcessPoolExecutor(max_workers=len(sources),mp_context=ctx) as pool:
                    futures=[pool.submit(worker,dsn,s,cap,gate) for s in sources]
                    rows=[f.result(60) for f in futures]
                counts={s:pb.used(s) for s in set(sources)}
                assert all(counts[s]==min(cap,sources.count(s))==sum(r[2] for r in rows if r[0]==s) for s in counts)
                report['tests'].append({'scenario':'process contention','workers':len(sources),'cap':cap,'counts':counts,'max_seconds':max(r[3] for r in rows),'batch_seconds_including_process_startup':time.monotonic()-batch_start,'reserved_calls_per_second_including_startup':sum(r[1] for r in rows)/(time.monotonic()-batch_start),'result':'PASS'})
            clear();gate=manager.Barrier(1);p=ctx.Process(target=worker,args=(dsn,'marketcheck',1,gate,True));p.start();p.join(30);assert p.exitcode==23 and pb.used('marketcheck')==1
            os.environ['MARKETCHECK_MONTHLY_CALL_BUDGET']='1'
            assert all(not pb.reserve('marketcheck') for _ in range(10))
            report['tests'].append({'scenario':'worker termination and ten exhausted attempts','result':'PASS'})
        clear()
        with patch.object(db.Conn,'commit',side_effect=RuntimeError('fixture commit error')):
            try:pb.reserve('marketcheck')
            except RuntimeError:pass
            else:raise AssertionError('commit failure authorized')
        assert pb.used('marketcheck')==0
        report['tests'].append({'scenario':'commit failure rollback','result':'PASS'})
        class Clock(datetime):
            instant=datetime(2026,12,31,23,59,59,tzinfo=timezone.utc)
            @classmethod
            def now(cls,tz=None):return cls.instant
        with patch.object(pb,'datetime',Clock):
            assert pb.reserve('marketcheck');Clock.instant=datetime(2027,1,1,tzinfo=timezone.utc);assert pb.reserve('marketcheck');assert not pb.reserve('marketcheck')
        report['tests'].append({'scenario':'UTC year rollover','result':'PASS'})
        # Deterministic unrelated-writer conflict: hold exact candidate lock.
        a=psycopg2.connect(dsn);b=psycopg2.connect(dsn)
        try:
            a.cursor().execute('LOCK TABLE usage_events IN SHARE ROW EXCLUSIVE MODE')
            diag=a.cursor();diag.execute("SELECT mode,granted FROM pg_locks WHERE relation='usage_events'::regclass");report['held_locks']=diag.fetchall()
            diag.execute("SELECT state,wait_event_type,wait_event FROM pg_stat_activity WHERE datname=current_database()");report['activity_snapshot']=diag.fetchall()
            cur=b.cursor();cur.execute("SET LOCAL lock_timeout='250ms'");t=time.monotonic()
            try:cur.execute("INSERT INTO usage_events(user_id,kind,units,cost_cents,created_at) VALUES(1,'unrelated',1,0,now())")
            except psycopg2.errors.LockNotAvailable:
                report['tests'].append({'scenario':'unrelated writer','result':'BLOCKED BY TABLE LOCK','observed_seconds':time.monotonic()-t})
                b.rollback()
            else:raise AssertionError('expected lock conflict absent')
            a.rollback();b.cursor().execute("INSERT INTO usage_events(user_id,kind,units,cost_cents,created_at) VALUES(1,'unrelated',1,0,now())");b.commit()
        finally:a.close();b.close()
    finally:
        pass  # Dedicated database is retained for owner inspection; no cleanup of external databases.
        Path('postgres-reservation-results.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
if __name__=='__main__':main()
