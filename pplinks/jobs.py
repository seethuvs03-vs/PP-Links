"""Durable SQLite queue, one worker; each item is committed independently."""
import json
import logging
import threading
from .db import connect, now
from .urls import normalize_url, platform
from .categories import load_rules, categorize
from .details import effective_details

log=logging.getLogger(__name__)

def submit_upload(db,filename,occurrences):
    with connect(db) as c:
        c.execute('BEGIN IMMEDIATE')
        job=c.execute('INSERT INTO jobs(filename,created_at,total) VALUES(?,?,?)',(filename,now(),len(occurrences))).lastrowid
        new=0
        for o in occurrences:
            known=c.execute('SELECT item_id FROM aliases WHERE url=?',(o['url'],)).fetchone()
            if known:item=known[0]
            else:
                item=c.execute('INSERT INTO items(url,original_url,platform,created_at,updated_at) VALUES(?,?,?,?,?)',
                               (o['url'],o['original_url'],platform(o['url']),now(),now())).lastrowid
                c.execute('INSERT INTO aliases VALUES(?,?)',(o['url'],item))
                c.execute('INSERT INTO job_items(job_id,item_id) VALUES(?,?)',(job,item));new+=1
            c.execute('INSERT INTO occurrences(job_id,item_id,sender,sent_at,timestamp_raw,message,original_url) VALUES(?,?,?,?,?,?,?)',
                      (job,item,o.get('sender',''),o.get('sent_at'),o.get('timestamp_raw',''),o.get('message',''),o['original_url']))
        c.execute('UPDATE jobs SET new=?,existing=?,work_total=?,status=? WHERE id=?',(new,len(occurrences)-new,new,'queued' if new else 'completed',job))
    return job

def retry_failed(db,item_id=None):
    with connect(db) as c:
        c.execute('BEGIN IMMEDIATE')
        rows=c.execute("SELECT id FROM items WHERE status IN ('failed','partial') AND platform != 'Other (legacy)'"+(' AND id=?' if item_id else ''),(item_id,) if item_id else ()).fetchall()
        if not rows:return None
        job=c.execute("INSERT INTO jobs(filename,created_at,kind,total,work_total) VALUES(?,?,'retry',?,?)",('Retry failed records',now(),len(rows),len(rows))).lastrowid
        for row in rows:
            c.execute('INSERT INTO job_items(job_id,item_id) VALUES(?,?)',(job,row[0]))
            c.execute("UPDATE items SET status='pending',error='' WHERE id=?",(row[0],))
    return job

def merge_items(c,loser,winner):
    """Keep all provenance and aliases when a redirect reveals an existing item."""
    c.execute('UPDATE occurrences SET item_id=? WHERE item_id=?',(winner,loser))
    c.execute('UPDATE legacy_rows SET item_id=? WHERE item_id=?',(winner,loser))
    c.execute('UPDATE detail_edits SET item_id=? WHERE item_id=?',(winner,loser))
    c.execute('UPDATE aliases SET item_id=? WHERE item_id=?',(winner,loser))
    for row in c.execute('SELECT * FROM job_items WHERE item_id=?',(loser,)).fetchall():
        c.execute('INSERT OR IGNORE INTO job_items(job_id,item_id,state) VALUES(?,?,?)',(row['job_id'],winner,row['state']))
    c.execute('DELETE FROM job_items WHERE item_id=?',(loser,))
    old=c.execute('SELECT * FROM items WHERE id=?',(loser,)).fetchone()
    target=c.execute('SELECT * FROM items WHERE id=?',(winner,)).fetchone()
    old_meta=json.loads(old['metadata']);new_meta=json.loads(target['metadata'])
    if old_meta.get('manual_details') and not new_meta.get('manual_details'):
        new_meta['manual_details']=old_meta['manual_details']
        new_meta['manual_updated_at']=old_meta.get('manual_updated_at')
        c.execute('UPDATE items SET metadata=? WHERE id=?',(json.dumps(new_meta,ensure_ascii=False),winner))
    if old['manual_category'] and not target['manual_category']:
        c.execute('UPDATE items SET category=?,subcategory=?,reason=?,manual_category=1 WHERE id=?',(old['category'],old['subcategory'],old['reason'],winner))
    c.execute('UPDATE items SET attempts=attempts+? WHERE id=?',(old['attempts'],winner))
    c.execute('DELETE FROM items WHERE id=?',(loser,))

class Worker:
    def __init__(self,db,categories,scraper,on_complete=None):
        self.db=db;self.categories=categories;self.scraper=scraper
        self.on_complete=on_complete
        self.event=threading.Event();self.stop_event=threading.Event();self.thread=None

    def start(self):
        # Only one server process is supported; uncommitted work resumes on restart.
        with connect(self.db) as c:
            c.execute("UPDATE jobs SET status='queued' WHERE status='running'")
            c.execute("UPDATE items SET status='pending' WHERE status='processing'")
            c.execute("UPDATE job_items SET state='queued' WHERE state='processing'")
        self.thread=threading.Thread(target=self.loop,daemon=True,name='pplinks-worker');self.thread.start()

    def loop(self):
        while not self.stop_event.is_set():
            try:
                with connect(self.db) as c:
                    job=c.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
                if job:self.process_job(job[0]);continue
            except Exception:
                log.exception('Worker loop error')
            self.event.wait(2);self.event.clear()

    def process_job(self,job):
        with connect(self.db) as c:
            c.execute("UPDATE jobs SET status='running' WHERE id=?",(job,))
        try:
            rules=load_rules(self.categories)
            while not self.stop_event.is_set():
                with connect(self.db) as c:
                    row=c.execute("SELECT i.* FROM items i JOIN job_items j ON j.item_id=i.id WHERE j.job_id=? AND j.state='queued' ORDER BY i.id LIMIT 1",(job,)).fetchone()
                    if row is None:
                        c.execute("UPDATE jobs SET status='completed' WHERE id=?",(job,));break
                    item=row['id']
                    if row['status']=='ok':
                        # Another queued upload may already have resolved this alias.
                        c.execute("UPDATE job_items SET state='done' WHERE job_id=? AND item_id=?",(job,item))
                        c.execute('UPDATE jobs SET processed=processed+1 WHERE id=?',(job,))
                        continue
                    c.execute("UPDATE job_items SET state='processing' WHERE job_id=? AND item_id=?",(job,item))
                    c.execute("UPDATE items SET status='processing',attempts=attempts+1 WHERE id=?",(item,))
                try:
                    metadata=self.scraper(row['url'])
                    category=categorize(metadata,rules)
                    final=normalize_url(metadata.get('final_url') or row['url'])
                    with connect(self.db) as c:
                        c.execute('BEGIN IMMEDIATE')
                        alias=c.execute('SELECT item_id FROM aliases WHERE url=?',(final,)).fetchone()
                        merged=False
                        if alias and alias[0]!=item:
                            winner=alias[0]
                            waiting=c.execute("SELECT 1 FROM job_items WHERE job_id=? AND item_id=? AND state='queued'",(job,winner)).fetchone()
                            merge_items(c,item,winner);item=winner;merged=True
                            if waiting:c.execute('UPDATE jobs SET processed=processed+1 WHERE id=?',(job,))
                            c.execute("UPDATE jobs SET new=MAX(0,new-1),existing=existing+1 WHERE id=? AND kind='upload'",(job,))
                        else:
                            c.execute('INSERT OR IGNORE INTO aliases VALUES(?,?)',(final,item))
                        previous=json.loads(c.execute('SELECT metadata FROM items WHERE id=?',(item,)).fetchone()[0])
                        previous.pop('retrieval_status',None);previous.pop('retrieval_note',None)
                        previous.update({k:v for k,v in metadata.items() if v not in (None,'',[],{})})
                        category=categorize(effective_details(previous),rules)
                        status='partial' if metadata.get('retrieval_status')=='partial' else 'ok'
                        c.execute("UPDATE items SET metadata=?,status=?,error=?,updated_at=? WHERE id=?",(json.dumps(previous,ensure_ascii=False),status,metadata.get('retrieval_note',''),now(),item))
                        c.execute('UPDATE items SET category=?,subcategory=?,keywords=?,confidence=?,reason=? WHERE id=? AND manual_category=0',
                                  (category['category'],category['subcategory'],json.dumps(category['keywords'],ensure_ascii=False),category['confidence'],category['reason'],item))
                        c.execute("UPDATE job_items SET state='done' WHERE job_id=? AND item_id=?",(job,item))
                        c.execute("UPDATE jobs SET processed=processed+1,updated=updated+CASE WHEN kind='retry' OR ? THEN 1 ELSE 0 END WHERE id=?",(merged,job))
                except Exception as exc:
                    log.warning('Job %s item %s failed: %s',job,item,type(exc).__name__)
                    with connect(self.db) as c:
                        c.execute("UPDATE items SET status='failed',error=?,updated_at=? WHERE id=?",(str(exc)[:2000],now(),item))
                        c.execute("UPDATE job_items SET state='failed' WHERE job_id=? AND item_id=?",(job,item))
                        c.execute('UPDATE jobs SET failed=failed+1,processed=processed+1 WHERE id=?',(job,))
        except Exception as exc:
            log.exception('Job %s failed',job)
            with connect(self.db) as c:
                c.execute("UPDATE items SET status='failed',error=? WHERE id IN (SELECT item_id FROM job_items WHERE job_id=? AND state IN ('queued','processing'))",(str(exc)[:2000],job))
                c.execute("UPDATE job_items SET state='failed' WHERE job_id=? AND state IN ('queued','processing')",(job,))
                c.execute("UPDATE jobs SET status='failed',error=?,failed=work_total-processed+failed,processed=work_total WHERE id=?",(str(exc)[:2000],job))

        finally:
            if self.on_complete:
                try:self.on_complete()
                except Exception:log.exception('Purchase workbook update failed')

    def wake(self):self.event.set()
    def stop(self):self.stop_event.set();self.event.set()
