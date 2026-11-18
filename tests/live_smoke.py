"""Optional real-network check. Reads three source URLs; never modifies the database."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pplinks.scraper import Scraper
from pplinks.db import connect

if __name__=='__main__':
    with connect('data/pplinks.sqlite3') as c:
        urls=[c.execute('SELECT url FROM items WHERE platform=? LIMIT 1',(p,)).fetchone() for p in ['Facebook','Instagram','YouTube']]
    results=[]
    for row in urls:
        if not row:continue
        try:
            metadata=Scraper(timeout=10,interval=1,retries=0)(row[0])
            results.append({'url':row[0],'status':'ok','fields':[k for k,v in metadata.items() if v]})
        except Exception as exc:results.append({'url':row[0],'status':'unavailable','reason':str(exc)[:350]})
    print(json.dumps(results,indent=2))
    Path('docs/live-smoke.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
