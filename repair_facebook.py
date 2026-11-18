"""Repair records affected by the old page-wide Facebook parser.

Run with the web server stopped. Backs up SQLite and records a normal retry job.
Reuses one standard Chrome window to avoid repeated browser startup during repair.
"""
import json
import sqlite3
from datetime import datetime
from pplinks import create_app
from pplinks.db import connect
from pplinks.facebook_details import generic_title
from pplinks.jobs import retry_failed
from pplinks.lock import InstanceLock

app=create_app(start_worker=False)
db=app.config['DATABASE']
lock=InstanceLock(db+'.lock').acquire()
backup='data/before-facebook-parser-repair-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3'
with sqlite3.connect(db) as source,sqlite3.connect(backup) as target:source.backup(target)
with connect(db) as c:
    affected=[]
    canonical_targets={r['url']:json.loads(r['metadata']).get('final_url') or r['url'] for r in c.execute('SELECT url,metadata FROM items')}
    for row in c.execute("SELECT id,metadata FROM items WHERE platform='Facebook'").fetchall():
        m=json.loads(row['metadata'])
        if not m.get('retrieval_method','').startswith('Selenium') or not generic_title(m.get('title')) or m.get('description'):continue
        affected.append(row['id'])
        for key in ('title','author','page_profile_channel','duration','posted_date','views','likes','username'):
            m.pop(key,None)
        c.execute("UPDATE items SET metadata=?,status='failed',error='Previous scraper captured page metadata instead of post details; retry required.' WHERE id=?",(json.dumps(m),row['id']))
print('BACKUP',backup,'AFFECTED',len(affected),flush=True)
worker=app.extensions['worker'];s=worker.scraper;session=app.extensions['facebook_session']
original_factory=s.make_driver
driver=None
try:
    with session.use_profile() as profile:
        if profile is None:raise RuntimeError('Connect Facebook before running the repair')
        driver=original_factory(profile=profile)
        class Lease:
            def __getattr__(self,name):return getattr(driver,name)
            def quit(self):pass
        s.make_driver=lambda **kwargs:Lease()
        s.fetch_page=lambda url:s._fetch_page(canonical_targets.get(url,url),profile)
        # The target-specific readiness loop still waits for delayed story data.
        s.render_wait=1
        job=retry_failed(db)
        if job is None:
            print('Nothing to retry.',flush=True)
            raise SystemExit(0)
        print('JOB',job,flush=True)
        import threading,time
        thread=threading.Thread(target=worker.process_job,args=(job,))
        thread.start()
        while thread.is_alive():
            thread.join(30)
            with connect(db) as c:
                row=c.execute('SELECT processed,work_total,updated,failed,status FROM jobs WHERE id=?',(job,)).fetchone()
                print('PROGRESS',dict(row),flush=True)
finally:
    if driver:driver.quit()
    session.stop()
