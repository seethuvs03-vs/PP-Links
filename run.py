"""One process, one durable worker. Waitress serves the local web interface."""
import argparse
import logging
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler
from pplinks import create_app

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--import-excel',metavar='PATH')
    parser.add_argument('--export',metavar='PATH')
    parser.add_argument('--init-db',action='store_true')
    parser.add_argument('--check-scraper',metavar='URL',help='Test one link without changing database records')
    args=parser.parse_args()
    command=bool(args.import_excel or args.export or args.init_db or args.check_scraper)
    Path('data').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s',handlers=[logging.StreamHandler(),RotatingFileHandler('data/app.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')])
    if not command or args.import_excel:
        from dotenv import load_dotenv
        from pplinks import ROOT
        from pplinks.lock import InstanceLock
        load_dotenv(ROOT/'.env')
        db=ROOT/os.getenv('DATABASE_PATH','data/pplinks.sqlite3')
        db.parent.mkdir(parents=True,exist_ok=True)
        instance_lock=InstanceLock(str(db)+'.lock').acquire()
    app=create_app(start_worker=not command)
    if args.check_scraper:
        from pplinks.urls import normalize_url
        import json
        try:
            result=app.extensions['worker'].scraper(normalize_url(args.check_scraper))
            print(json.dumps({'status':'ok','method':result.get('retrieval_method','HTTP'),'title':result.get('title'),'fields':[k for k,v in result.items() if v]},ensure_ascii=True))
        except Exception as exc:
            print(json.dumps({'status':'unavailable','error':str(exc)},ensure_ascii=True))
            raise SystemExit(1)
    if args.import_excel:
        from pplinks.importer import import_workbook
        print(import_workbook(app.config['DATABASE'],args.import_excel,app.config['CATEGORIES']))
    if args.export:
        from pplinks.export import export_excel
        target=Path(args.export);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(export_excel(app.config['DATABASE']).getvalue());print(target.resolve())
    if not command:
        from waitress import serve
        port=int(os.getenv('PORT','5000'))
        host=os.getenv('HOST','127.0.0.1')
        print(f'PP Links is listening on {host}:{port}',flush=True)
        try:serve(app,host=host,port=port,threads=4)
        finally:
            app.extensions['worker'].stop()
            if app.extensions.get('facebook_session'):app.extensions['facebook_session'].stop()
