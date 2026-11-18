"""Restore retained import values overwritten by the old generic-title scrape."""
import json,sqlite3
from datetime import datetime
from pplinks.db import connect,now
from pplinks.export import KEYS
from pplinks.categories import load_rules,categorize
from pplinks.details import effective_details
db='data/pplinks.sqlite3'
with sqlite3.connect(db) as source,sqlite3.connect('data/before-import-field-restore-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3') as backup:source.backup(backup)
rules=load_rules('config/categories.json')
count=0
with connect(db) as c:
    c.execute('BEGIN IMMEDIATE')
    for row in c.execute("SELECT * FROM items WHERE json_extract(metadata,'$.title')='(2) Facebook'").fetchall():
        original={}
        for legacy in c.execute('SELECT raw_json FROM legacy_rows WHERE item_id=? ORDER BY id',(row['id'],)):
            original.update({k:v for k,v in zip(KEYS,json.loads(legacy[0])[1:]) if v not in (None,'')})
        if not original.get('title') and not original.get('description'):continue
        metadata=json.loads(row['metadata'])
        for key in ('title','author','description','views','likes','duration','posted_date','thumbnail'):
            metadata.pop(key,None)
            if key in original:metadata[key]=original[key]
        for key in ('retrieval_method','retrieved_at','page_profile_channel','username','meta_tags','structured_data','media_type','keywords','hashtags','external_links'):
            metadata.pop(key,None)
        metadata['source']='Legacy workbook (not reverified)'
        metadata['restored_from_import_at']=now()
        c.execute("UPDATE items SET metadata=?,updated_at=? WHERE id=?",(json.dumps(metadata,ensure_ascii=False),now(),row['id']))
        category=categorize(effective_details(metadata),rules)
        c.execute('UPDATE items SET category=?,subcategory=?,keywords=?,confidence=?,reason=? WHERE id=? AND manual_category=0',
                  (category['category'],category['subcategory'],json.dumps(category['keywords']),category['confidence'],category['reason'],row['id']))
        count+=1
print('Restored original imported fields:',count)
