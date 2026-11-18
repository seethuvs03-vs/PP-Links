import json
import os
import secrets
import hashlib
from pathlib import Path
from flask import Flask,render_template,request,redirect,url_for,flash,session,abort,jsonify,send_file
from dotenv import load_dotenv
from .db import init_db,connect,now
from .jobs import Worker,submit_upload,retry_failed
from .scraper import Scraper
from .whatsapp import parse_export,decode_upload
from .categories import load_rules,categorize
from .export import export_excel
from .details import effective_details

ROOT=Path(__file__).resolve().parent.parent

def create_app(config=None,start_worker=True):
    load_dotenv(ROOT/'.env')
    app=Flask(__name__)
    app.config.update(SECRET_KEY=os.getenv('SECRET_KEY') or secrets.token_hex(32),
        DATABASE=str(ROOT/os.getenv('DATABASE_PATH','data/pplinks.sqlite3')),
        CATEGORIES=str(ROOT/os.getenv('CATEGORIES_PATH','config/categories.json')),
        MAX_CONTENT_LENGTH=int(os.getenv('MAX_UPLOAD_MB','10'))*1024*1024,
        DOCUMENT_MAX_FILE_MB=int(os.getenv('DOCUMENT_MAX_FILE_MB','20')),
        DOCUMENT_MAX_BATCH_MB=int(os.getenv('DOCUMENT_MAX_BATCH_MB','50')),
        DOCUMENTS_DIR=None,
        SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict',
        TRUSTED_HOSTS=[h.strip() for h in os.getenv('TRUSTED_HOSTS','localhost,127.0.0.1,[::1]').split(',') if h.strip()],
        SCRAPER_BACKEND=os.getenv('SCRAPER_BACKEND','selenium').lower(),
        RESPECT_ROBOTS=os.getenv('RESPECT_ROBOTS','true').lower()=='true')
    if config:app.config.update(config)
    init_db(app.config['DATABASE']);load_rules(app.config['CATEGORIES'])
    from . import documents
    app.config['DOCUMENTS_DIR']=app.config['DOCUMENTS_DIR'] or os.getenv('DOCUMENTS_DIR') or str(Path(app.config['DATABASE']).parent/'documents')
    documents.initialize(app.config['DATABASE']);app.register_blueprint(documents.bp)
    scraper_options=dict(timeout=float(os.getenv('REQUEST_TIMEOUT','20')),interval=float(os.getenv('REQUEST_INTERVAL','3')),retries=int(os.getenv('REQUEST_RETRIES','2')),respect_robots=app.config['RESPECT_ROBOTS'])
    if app.config['SCRAPER_BACKEND']=='selenium':
        from .selenium_scraper import SeleniumScraper
        scraper=SeleniumScraper(headless=os.getenv('SELENIUM_HEADLESS','true').lower()=='true',
            max_page_mb=int(os.getenv('SELENIUM_MAX_PAGE_MB','32')),
            render_wait=float(os.getenv('SELENIUM_RENDER_WAIT','3')),
            chrome_binary=os.getenv('CHROME_BINARY',''),driver_path=os.getenv('CHROMEDRIVER_PATH',''),**scraper_options)
    elif app.config['SCRAPER_BACKEND']=='http':scraper=Scraper(**scraper_options)
    else:raise ValueError('SCRAPER_BACKEND must be selenium or http')
    facebook=None
    if app.config['SCRAPER_BACKEND']=='selenium':
        from .facebook_session import FacebookSession
        identity=hashlib.sha256(str(Path(app.config['DATABASE']).resolve()).encode()).hexdigest()[:16]
        session_root=Path(os.getenv('LOCALAPPDATA',str(Path.home()/'.local/share')))/'PPLinks'/'Sessions'/identity
        facebook=FacebookSession(app.config.get('FACEBOOK_SESSION_DIR',session_root),scraper.make_driver)
        scraper.facebook_session=facebook
    app.extensions['facebook_session']=facebook
    from .purchase_file import PurchaseFile
    purchase=PurchaseFile(app.config['DATABASE'],app.config.get('PURCHASE_MASTER_PATH') or ROOT/os.getenv('PURCHASE_MASTER_PATH','outputs/Purchase-Team-Master.xlsx'))
    app.extensions['purchase_file']=purchase
    worker=Worker(app.config['DATABASE'],app.config['CATEGORIES'],scraper,on_complete=purchase.sync)
    app.extensions['worker']=worker
    if start_worker:worker.start()

    @app.before_request
    def protect():
        if request.path in ('/upload','/documents/upload'):
            request.max_content_length=app.config['DOCUMENT_MAX_BATCH_MB']*1024*1024
        if 'csrf' not in session:session['csrf']=secrets.token_hex(32)
        if request.method=='POST' and not secrets.compare_digest(request.form.get('csrf',''),session['csrf']):abort(400,description='Form expired. Reload the page and try again.')

    @app.after_request
    def headers(response):
        if request.method=='POST' and response.status_code<400 and request.endpoint in ('edit_details','edit_category','reclassify'):
            purchase.sync()
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-src https://www.facebook.com; frame-ancestors 'none'; form-action 'self'; base-uri 'self'"
        return response

    @app.context_processor
    def context():return {'csrf':session.get('csrf',''),'purchase_error':purchase.error}

    @app.errorhandler(413)
    def too_large(e):return render_template('error.html',message='Upload exceeds the configured size limit.'),413

    @app.errorhandler(400)
    def invalid(e):
        from werkzeug.exceptions import SecurityError
        if isinstance(e,SecurityError):return 'Untrusted host',400
        return render_template('error.html',message=e.description),400

    @app.get('/')
    def dashboard():
        with connect(app.config['DATABASE']) as c:
            counts={r['status']:r['n'] for r in c.execute('SELECT status,COUNT(*) n FROM items GROUP BY status')}
            jobs=c.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 6').fetchall()
            categories=c.execute('SELECT category,COUNT(*) n FROM items GROUP BY category ORDER BY n DESC').fetchall()
        return render_template('dashboard.html',counts=counts,total=sum(counts.values()),jobs=jobs,categories=categories)

    @app.post('/upload')
    def upload():
        f=request.files.get('file')
        if not f or not f.filename.lower().endswith('.txt'):abort(400,description='Select a WhatsApp .txt export.')
        mode=request.form.get('mode','links')
        if mode not in ('links','documents'):abort(400,description='Choose links or documents.')
        if mode=='documents':
            try:
                new,existing,refs=documents.store_upload(f,request.files.getlist('attachments'),request.form.get('date_order','DMY'))
            except (ValueError,UnicodeError) as exc:abort(400,description=str(exc))
            flash(f'Documents saved: {new} new, {existing} duplicate files, {refs} attachment references. Open attachment references to upload any missing files.')
            return redirect(url_for('documents.library'))
        if any(a.filename for a in request.files.getlist('attachments')):
            abort(400,description='Upload attachment files in Documents. This TXT upload processes links.')
        try:
            order=request.form.get('date_order','DMY')
            if order not in ('DMY','MDY'):raise ValueError('Choose a date order')
            raw=f.read(app.config['MAX_CONTENT_LENGTH']+1)
            if len(raw)>app.config['MAX_CONTENT_LENGTH']:raise ValueError('Chat TXT exceeds the upload size limit.')
            text=decode_upload(raw)
            occurrences=parse_export(text,order)
            if not occurrences:raise ValueError('No supported Instagram, Facebook or YouTube links found.')
            if len(occurrences)>25000:raise ValueError('Maximum 25,000 link occurrences per upload.')
        except (ValueError,UnicodeError) as exc:abort(400,description=str(exc))
        filename=f.filename.replace('\\','/').split('/')[-1][:255]
        job=submit_upload(app.config['DATABASE'],filename,occurrences);worker.wake()
        with connect(app.config['DATABASE']) as c:
            unresolved=c.execute("SELECT COUNT(DISTINCT i.id) FROM items i JOIN occurrences o ON o.item_id=i.id WHERE o.job_id=? AND i.status='failed'",(job,)).fetchone()[0]
        if unresolved:flash(f'{unresolved} previously saved links in this upload still have unavailable platform details. Open All links and filter by failed to add details; uploading again does not retrieve blocked content.')
        return redirect(url_for('history',job=job))

    @app.post('/paste-links')
    def paste_links():
        raw=request.form.get('links','').strip()
        if not raw:abort(400,description='Paste at least one Facebook, Instagram or YouTube link.')
        if len(raw)>100000:abort(400,description='Paste at most 100,000 characters; use TXT upload for larger lists.')
        try:
            text=decode_upload(raw.encode('utf-8'))
            occurrences=parse_export(text)
        except (ValueError,UnicodeError) as exc:abort(400,description=str(exc))
        if not occurrences:abort(400,description='No supported Facebook, Instagram or YouTube links found.')
        if len(occurrences)>1000:abort(400,description='Paste at most 1,000 links at a time.')
        job=submit_upload(app.config['DATABASE'],'Pasted links',occurrences)
        worker.wake()
        flash('Pasted links saved. New links will be processed and added to the purchase master. Existing links are not duplicated; use Retry for existing failed records.')
        return redirect(url_for('history',job=job))

    @app.get('/results')
    def results():
        clauses=[];args=[]
        q=request.args.get('q','').strip()[:200]
        if q:clauses.append('(metadata LIKE ? OR original_url LIKE ? OR category LIKE ?)');args.extend(['%'+q+'%']*3)
        for field in ('platform','category','status'):
            if request.args.get(field):clauses.append(field+'=?');args.append(request.args[field])
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        page=max(1,request.args.get('page',1,type=int));limit=40
        with connect(app.config['DATABASE']) as c:
            count=c.execute('SELECT COUNT(*) FROM items'+where,args).fetchone()[0]
            rows=[dict(r) for r in c.execute('SELECT * FROM items'+where+' ORDER BY id DESC LIMIT ? OFFSET ?',args+[limit,(page-1)*limit])]
            categories=[r[0] for r in c.execute('SELECT DISTINCT category FROM items ORDER BY category')]
        for row in rows:row['details']=effective_details(json.loads(row['metadata']))
        return render_template('results.html',items=rows,count=count,page=page,pages=max(1,(count+limit-1)//limit),categories=categories)

    @app.get('/connections')
    def connections():
        with connect(app.config['DATABASE']) as c:
            failed=c.execute("SELECT id,original_url FROM items WHERE platform='Facebook' AND status IN ('failed','partial') ORDER BY id DESC LIMIT 40").fetchall()
        return render_template('connections.html',connection=facebook.snapshot() if facebook else None,failed=failed)

    @app.get('/api/facebook-session')
    def facebook_status():return jsonify(facebook.snapshot() if facebook else {'state':'disabled'})

    @app.post('/connections/<action>')
    def connection_action(action):
        if facebook is None:abort(400,description='Select the Selenium backend to connect Facebook.')
        actions={'connect':facebook.begin,'finish':facebook.finish,'cancel':facebook.cancel,'disconnect':facebook.clear}
        if action not in actions:abort(404)
        try:actions[action]()
        except ValueError as exc:flash(str(exc))
        except OSError:flash('Chrome session files could not be updated. Close the sign-in window and try again.')
        return redirect(url_for('connections'))

    @app.get('/items/<int:item_id>')
    def item(item_id):
        with connect(app.config['DATABASE']) as c:
            row=c.execute('SELECT * FROM items WHERE id=?',(item_id,)).fetchone()
            if not row:abort(404)
            occurrences=c.execute('SELECT o.*,j.filename FROM occurrences o JOIN jobs j ON j.id=o.job_id WHERE item_id=? ORDER BY o.id',(item_id,)).fetchall()
            legacy=c.execute('SELECT * FROM legacy_rows WHERE item_id=?',(item_id,)).fetchall()
            aliases=c.execute('SELECT url FROM aliases WHERE item_id=?',(item_id,)).fetchall()
        from urllib.parse import urlencode
        preview='https://www.facebook.com/plugins/video.php?'+urlencode({'href':row['original_url'],'show_text':'true','width':'500'}) if row['platform']=='Facebook' else None
        return render_template('item.html',item=row,details=effective_details(json.loads(row['metadata'])),occurrences=occurrences,legacy=legacy,aliases=aliases,preview_url=preview)

    @app.post('/items/<int:item_id>/details')
    def edit_details(item_id):
        fields=('title','author','description','caption','transcript','location')
        manual={k:request.form.get(k,'').strip() for k in fields}
        if not manual['title'] and not manual['description']:
            abort(400,description='Enter a title or description copied from the post.')
        if any(len(v)>30000 for v in manual.values()):abort(400,description='Use at most 30,000 characters per field.')
        manual={k:v for k,v in manual.items() if v}
        rules=load_rules(app.config['CATEGORIES'])
        with connect(app.config['DATABASE']) as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM items WHERE id=?',(item_id,)).fetchone()
            if not row:abort(404)
            metadata=json.loads(row['metadata'])
            c.execute('INSERT INTO detail_edits(item_id,created_at,details) VALUES(?,?,?)',(item_id,now(),json.dumps(manual,ensure_ascii=False)))
            metadata['manual_details']=manual;metadata['manual_updated_at']=now()
            c.execute('UPDATE items SET metadata=?,updated_at=? WHERE id=?',(json.dumps(metadata,ensure_ascii=False),now(),item_id))
            result=categorize(effective_details(metadata),rules)
            c.execute('UPDATE items SET category=?,subcategory=?,keywords=?,confidence=NULL,reason=? WHERE id=? AND manual_category=0',
                      (result['category'],result['subcategory'],json.dumps(result['keywords'],ensure_ascii=False),'Based on saved details: '+result['reason'],item_id))
        flash('Details saved and included in Excel. They are labelled user-entered; the platform retrieval result has not been changed.')
        return redirect(url_for('item',item_id=item_id))

    @app.post('/items/<int:item_id>/category')
    def edit_category(item_id):
        category=request.form.get('category','').strip();subcategory=request.form.get('subcategory','').strip()
        if not category or len(category)>100 or len(subcategory)>100:abort(400,description='Category is required; use at most 100 characters per field.')
        with connect(app.config['DATABASE']) as c:
            updated=c.execute("UPDATE items SET category=?,subcategory=?,manual_category=1,reason='Manually edited',confidence=NULL,updated_at=? WHERE id=?",(category,subcategory,now(),item_id))
            if not updated.rowcount:abort(404)
        flash('Category saved. Future retries will preserve your edit.');return redirect(url_for('item',item_id=item_id))

    @app.post('/retry')
    def retry():
        item_id=request.form.get('item_id',type=int)
        job=retry_failed(app.config['DATABASE'],item_id);worker.wake()
        if not job:flash('No failed or incomplete records are available to retry.')
        return redirect(url_for('history'))

    @app.get('/history')
    def history():
        with connect(app.config['DATABASE']) as c:jobs=c.execute('SELECT * FROM jobs ORDER BY id DESC').fetchall()
        return render_template('history.html',jobs=jobs)

    @app.get('/import-audit')
    def import_audit():
        with connect(app.config['DATABASE']) as c:
            audit=c.execute('SELECT * FROM legacy_rows ORDER BY id').fetchall()
        return render_template('audit.html',audit=audit)

    @app.get('/api/jobs')
    def jobs_api():
        with connect(app.config['DATABASE']) as c:rows=[dict(r) for r in c.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 100')]
        return jsonify(rows)

    @app.get('/export.xlsx')
    def download():return send_file(export_excel(app.config['DATABASE']),as_attachment=True,download_name='pplinks-master.xlsx',mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @app.route('/purchase-file',methods=['GET','POST'])
    def purchase_file():
        if request.method=='POST':
            if purchase.sync():flash('Purchase master updated in the same file.')
            else:flash(purchase.error)
            return redirect(url_for('purchase_file'))
        return render_template('purchase_file.html',purchase=purchase)

    @app.get('/purchase-master.xlsx')
    def purchase_download():
        if not purchase.path.exists():abort(404,description='Create the purchase master from the Purchase master page first.')
        return send_file(purchase.path.resolve(),as_attachment=True,download_name=purchase.path.name)

    @app.get('/items/<int:item_id>/metadata.json')
    def metadata_json(item_id):
        with connect(app.config['DATABASE']) as c:
            row=c.execute('SELECT metadata FROM items WHERE id=?',(item_id,)).fetchone()
        if not row:abort(404)
        return jsonify(json.loads(row[0]))

    @app.route('/categories',methods=['GET','POST'])
    def category_settings():
        path=Path(app.config['CATEGORIES'])
        if request.method=='POST':
            raw=request.form.get('rules','')
            if len(raw)>100000:abort(400,description='Category configuration is too large.')
            temp=path.with_name(path.name+'.'+secrets.token_hex(8)+'.tmp')
            try:
                temp.write_text(raw,encoding='utf-8');load_rules(temp);temp.replace(path)
            except (ValueError,TypeError,KeyError) as exc:abort(400,description=str(exc))
            finally:temp.unlink(missing_ok=True)
            flash('Rules saved. New processing uses these rules. Reclassify existing records below if needed.')
            return redirect(url_for('category_settings'))
        return render_template('categories.html',rules=path.read_text(encoding='utf-8'))

    @app.post('/categories/reclassify')
    def reclassify():
        config=load_rules(app.config['CATEGORIES'])
        with connect(app.config['DATABASE']) as c:
            for row in c.execute('SELECT id,metadata FROM items WHERE manual_category=0').fetchall():
                result=categorize(effective_details(json.loads(row['metadata'])),config)
                c.execute('UPDATE items SET category=?,subcategory=?,keywords=?,confidence=?,reason=?,updated_at=? WHERE id=?',
                          (result['category'],result['subcategory'],json.dumps(result['keywords']),result['confidence'],result['reason'],now(),row['id']))
        flash('Automatic categories updated; manual edits preserved.');return redirect(url_for('category_settings'))

    @app.get('/health')
    def health():return {'status':'ok','scraper_backend':app.config['SCRAPER_BACKEND'],'respect_robots':app.config['RESPECT_ROBOTS']}
    return app
