"""Explicit, idempotent import; retain every source row for audit."""
import json
from pathlib import Path
import openpyxl
from urllib.parse import urlsplit,urlunsplit
from .db import connect,now
from .urls import normalize_url,platform
from .categories import load_rules,categorize
from .export import HEADERS,KEYS
from .jobs import merge_items

def legacy_url(value):
    """Keep existing external links as archival records, never as scraper targets."""
    try:return normalize_url(value),platform(value)
    except ValueError:
        p=urlsplit(value)
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:raise ValueError('No usable URL')
        return urlunsplit((p.scheme,p.netloc.lower(),p.path,p.query,'')),'Other (legacy)'

def import_workbook(db,path,categories):
    wb=openpyxl.load_workbook(path,data_only=False)
    ws=wb['Facebook Reels']
    if [c.value for c in ws[1]]!=HEADERS:raise ValueError('Workbook headers do not match the reviewed 13-column format')
    rules=load_rules(categories)
    import hashlib
    fingerprint=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    source=Path(path).name+'#'+fingerprint
    with connect(db) as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM legacy_rows WHERE source=?',(source,)).fetchone():return {'already_imported':True}
        job=c.execute("INSERT INTO jobs(filename,created_at,kind,status) VALUES(?,?,'legacy import','completed')",(Path(path).name,now())).lastrowid
        total=new=0
        for index,row in enumerate(ws.iter_rows(min_row=2,values_only=True),2):
            item=None;note='';raw=json.dumps(row,ensure_ascii=False,default=str)
            try:
                url,item_platform=legacy_url(str(row[1] or ''))
            except ValueError:
                note='Repeated header' if list(row)==HEADERS else 'No supported URL; retained without fabricating a link'
            else:
                total+=1
                try:final,_=legacy_url(str(row[2] or url))
                except ValueError:final=url
                aliases=c.execute('SELECT DISTINCT item_id FROM aliases WHERE url IN (?,?)',(url,final)).fetchall()
                if aliases:
                    item=aliases[0][0]
                    for other in aliases[1:]:merge_items(c,other[0],item)
                else:
                    item=c.execute('INSERT INTO items(url,original_url,platform,created_at,updated_at) VALUES(?,?,?,?,?)',(final,str(row[1]),item_platform,now(),now())).lastrowid;new+=1
                for alias in (url,final):c.execute('INSERT OR IGNORE INTO aliases VALUES(?,?)',(alias,item))
                metadata=dict(zip(KEYS,row[1:]));metadata['platform']=item_platform;metadata['source']='Legacy workbook (not reverified)'
                category=categorize(metadata,rules)
                prior=json.loads(c.execute('SELECT metadata FROM items WHERE id=?',(item,)).fetchone()[0])
                prior.update({k:v for k,v in metadata.items() if v not in (None,'')})
                ok=bool(metadata.get('title') or metadata.get('description')) and metadata.get('status')!='ERROR'
                c.execute('UPDATE items SET metadata=?,status=?,error=?,updated_at=? WHERE id=?',
                          (json.dumps(prior,ensure_ascii=False,default=str),'ok' if ok else 'failed','' if ok else str(metadata.get('error') or 'Legacy row has no retrieved content'),now(),item))
                c.execute('UPDATE items SET category=?,subcategory=?,keywords=?,reason=? WHERE id=? AND manual_category=0',
                          (category['category'],category['subcategory'],json.dumps(category['keywords'],ensure_ascii=False),category['reason'],item))
                note='Imported source values; missing status inferred from presence of title/description' if not row[11] else 'Imported; not reverified'
            c.execute('INSERT INTO legacy_rows(source,row_number,raw_json,item_id,note) VALUES(?,?,?,?,?)',(source,index,raw,item,note))
        failed=c.execute("SELECT COUNT(DISTINCT item_id) FROM legacy_rows JOIN items ON items.id=legacy_rows.item_id WHERE source=? AND items.status='failed'",(source,)).fetchone()[0]
        c.execute('UPDATE jobs SET total=?,new=?,existing=?,failed=? WHERE id=?',(total,new,total-new,failed,job))
    return dict(total=total,new=new,existing=total-new,failed=failed,job_id=job)
