"""Persistent attachment library. TXT references never stand in for uploaded bytes."""
import hashlib
import io
import json
import re
import uuid
import zipfile
from pathlib import Path
from flask import Blueprint, current_app, request, render_template, abort, redirect, url_for, flash, send_file
from .db import connect, now
from .whatsapp import decode_upload, parse_messages

bp=Blueprint('documents',__name__)
KINDS={'.pdf':'PDF','.doc':'Word','.docx':'Word','.xls':'Excel','.xlsx':'Excel','.csv':'Excel',
       '.jpg':'Image','.jpeg':'Image','.png':'Image','.gif':'Image','.webp':'Image','.bmp':'Image'}
ACCEPT=','.join(KINDS)
IMAGE_MIMES={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png',
             '.gif':'image/gif','.webp':'image/webp','.bmp':'image/bmp'}

def basename(name):
    return re.sub(r'[\x00-\x1f\x7f]','_', (name or '').replace('\\','/').split('/')[-1]).strip()[:255]

def initialize(db):
    with connect(db) as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS documents(
          id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, filename TEXT NOT NULL,
          kind TEXT NOT NULL, size INTEGER NOT NULL, storage_name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS document_uploads(
          id INTEGER PRIMARY KEY, filename TEXT NOT NULL, created_at TEXT NOT NULL,
          new_files INTEGER NOT NULL, existing_files INTEGER NOT NULL, references_found INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS document_upload_files(
          upload_id INTEGER REFERENCES document_uploads(id), document_id INTEGER REFERENCES documents(id), filename TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS document_references(
          id INTEGER PRIMARY KEY, identity TEXT UNIQUE NOT NULL, chat_filename TEXT NOT NULL,
          filename TEXT NOT NULL, sender TEXT, sent_at TEXT, timestamp_raw TEXT, message TEXT NOT NULL,
          document_id INTEGER REFERENCES documents(id), created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_document_ref_file ON document_references(document_id);
        ''')

def attachment_references(text,order='DMY'):
    found=[]
    for msg in parse_messages(text,order):
        for line in msg['message'].splitlines():
            line=line.strip().replace('\u200e','').replace('\u200f','')
            # Common iOS <attached: name.ext> and Android name.ext (file attached).
            marked=re.search(r'<attached:\s*(.*?)>',line,re.I)
            name=marked.group(1) if marked else re.sub(r'\s*\((?:file )?attached\)\s*$','',line,flags=re.I)
            name=basename(name)
            if '://' in line or Path(name).suffix.lower() not in KINDS:continue
            found.append({**msg,'filename':name})
    return found

def validated(file,limit):
    name=basename(file.filename);ext=Path(name).suffix.lower()
    if ext not in KINDS:raise ValueError(f'{name}: upload Excel, PDF, Word or a supported image file.')
    data=file.stream.read(limit+1)
    if not data:raise ValueError(f'{name}: file is empty.')
    if len(data)>limit:raise ValueError(f'{name}: exceeds the per-file size limit.')
    valid=False
    if ext=='.pdf':valid=data.startswith(b'%PDF-')
    elif ext in ('.doc','.xls'):valid=data.startswith(bytes.fromhex('D0CF11E0A1B11AE1'))
    elif ext in ('.docx','.xlsx'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names=set(archive.namelist())
                required='word/document.xml' if ext=='.docx' else 'xl/workbook.xml'
                valid='[Content_Types].xml' in names and required in names and not any(n.lower().endswith('vbaproject.bin') for n in names)
        except (zipfile.BadZipFile,ValueError):pass
    elif ext=='.csv':
        try:decode_upload(data);valid=True
        except (ValueError,UnicodeError):pass
    elif ext in ('.jpg','.jpeg'):valid=data.startswith(b'\xff\xd8\xff')
    elif ext=='.png':valid=data.startswith(b'\x89PNG\r\n\x1a\n')
    elif ext=='.gif':valid=data[:6] in (b'GIF87a',b'GIF89a')
    elif ext=='.webp':valid=data[:4]==b'RIFF' and data[8:12]==b'WEBP'
    elif ext=='.bmp':valid=data.startswith(b'BM')
    if not valid:raise ValueError(f'{name}: file contents do not match its supported format.')
    return name,ext,data,hashlib.sha256(data).hexdigest()

def store_upload(chat,files,order='DMY',reference_id=None):
    cfg=current_app.config
    files=[f for f in files if f.filename]
    if len(files)>50:raise ValueError('Upload at most 50 files at a time.')
    if order not in ('DMY','MDY'):raise ValueError('Choose a valid date order.')
    refs=[];chat_name='Direct document upload'
    if chat and chat.filename:
        if not chat.filename.lower().endswith('.txt'):raise ValueError('Select a WhatsApp .txt export.')
        raw=chat.stream.read(cfg['MAX_CONTENT_LENGTH']+1)
        if len(raw)>cfg['MAX_CONTENT_LENGTH']:raise ValueError('Chat TXT exceeds the upload size limit.')
        refs=attachment_references(decode_upload(raw),order);chat_name=basename(chat.filename)
        if len(refs)>25000:raise ValueError('Maximum 25,000 attachment references per chat.')
    if not refs and not files:raise ValueError('No supported attachment filenames found. TXT cannot contain the attachments themselves; upload the actual files below.')
    if reference_id and (len(files)!=1 or chat):raise ValueError('Select exactly one file to attach to this reference.')
    prepared=[validated(f,cfg['DOCUMENT_MAX_FILE_MB']*1024*1024) for f in files]
    root=Path(cfg['DOCUMENTS_DIR']).resolve();root.mkdir(parents=True,exist_ok=True)
    created=[];new=existing=0;by_name={}
    try:
        with connect(cfg['DATABASE']) as c:
            c.execute('BEGIN IMMEDIATE')
            if reference_id and not c.execute('SELECT id FROM document_references WHERE id=?',(reference_id,)).fetchone():
                raise ValueError('Attachment reference not found.')
            uploaded=[]
            for name,ext,data,digest in prepared:
                row=c.execute('SELECT id,storage_name FROM documents WHERE sha256=?',(digest,)).fetchone()
                if row:
                    doc_id=row['id'];existing+=1
                    if not (root/row['storage_name']).is_file():raise ValueError('A stored file is missing. Restore the document storage folder from backup.')
                else:
                    storage=uuid.uuid4().hex+ext;path=root/storage
                    created.append(path);path.write_bytes(data)
                    doc_id=c.execute('INSERT INTO documents(sha256,filename,kind,size,storage_name,created_at) VALUES(?,?,?,?,?,?)',
                        (digest,name,KINDS[ext],len(data),storage,now())).lastrowid;new+=1
                by_name.setdefault(name.casefold(),set()).add(doc_id);uploaded.append((doc_id,name))
            for ref in refs:
                identity=hashlib.sha256(json.dumps([chat_name,ref],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                matches=by_name.get(ref['filename'].casefold(),set());doc_id=next(iter(matches)) if len(matches)==1 else None
                c.execute('INSERT OR IGNORE INTO document_references(identity,chat_filename,filename,sender,sent_at,timestamp_raw,message,document_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (identity,chat_name,ref['filename'],ref['sender'],ref['sent_at'],ref['timestamp_raw'],ref['message'],doc_id,now()))
                if doc_id:c.execute('UPDATE document_references SET document_id=? WHERE identity=? AND document_id IS NULL',(doc_id,identity))
            if reference_id:
                old=c.execute('SELECT document_id FROM document_references WHERE id=?',(reference_id,)).fetchone()[0]
                if old and old!=uploaded[0][0]:raise ValueError('This reference already has a different file attached.')
                c.execute('UPDATE document_references SET document_id=? WHERE id=?',(uploaded[0][0],reference_id))
            upload_id=c.execute('INSERT INTO document_uploads(filename,created_at,new_files,existing_files,references_found) VALUES(?,?,?,?,?)',
                (chat_name,now(),new,existing,len(refs))).lastrowid
            c.executemany('INSERT INTO document_upload_files VALUES(?,?,?)',[(upload_id,i,n) for i,n in uploaded])
    except Exception:
        for path in created:path.unlink(missing_ok=True)
        raise
    return new,existing,len(refs)

@bp.get('/documents')
def library():
    q=request.args.get('q','').strip()[:200];kind=request.args.get('kind','')
    page=max(1,request.args.get('page',1,type=int));where=[];args=[]
    if q:
        where.append('(d.filename LIKE ? OR EXISTS (SELECT 1 FROM document_upload_files u WHERE u.document_id=d.id AND u.filename LIKE ?))');args+=['%'+q+'%']*2
    if kind in set(KINDS.values()):where.append('d.kind=?');args.append(kind)
    clause=' WHERE '+' AND '.join(where) if where else ''
    with connect(current_app.config['DATABASE']) as c:
        count=c.execute('SELECT COUNT(*) FROM documents d'+clause,args).fetchone()[0]
        docs=c.execute('SELECT d.* FROM documents d'+clause+' ORDER BY d.id DESC LIMIT 40 OFFSET ?',args+[(page-1)*40]).fetchall()
        missing=c.execute('SELECT COUNT(*) FROM document_references WHERE document_id IS NULL').fetchone()[0]
        uploads=c.execute('SELECT * FROM document_uploads ORDER BY id DESC LIMIT 10').fetchall()
    return render_template('documents.html',documents=docs,count=count,missing=missing,uploads=uploads,accept=ACCEPT,page=page,pages=max(1,(count+39)//40),view='list' if request.args.get('view')=='list' else 'gallery')

@bp.post('/documents/upload')
def upload():
    try:
        ref=request.form.get('reference_id','')
        if ref and (len(ref)>18 or not ref.isdecimal() or int(ref)<1):raise ValueError('Invalid attachment reference.')
        result=store_upload(request.files.get('chat'),request.files.getlist('attachments'),request.form.get('date_order','DMY'),int(ref) if ref else None)
    except (ValueError,UnicodeError) as exc:abort(400,description=str(exc))
    flash(f'Documents saved: {result[0]} new, {result[1]} duplicate files, {result[2]} chat attachment references. References without a matched file remain awaiting upload.')
    return redirect(url_for('documents.library'))

@bp.get('/documents/references')
def references():
    q=request.args.get('q','').strip()[:200];page=max(1,request.args.get('page',1,type=int));args=[];where=[]
    if request.args.get('status','missing')=='missing':where.append('r.document_id IS NULL')
    if q:where.append('(r.filename LIKE ? OR r.sender LIKE ? OR r.chat_filename LIKE ?)');args+=['%'+q+'%']*3
    clause=' WHERE '+' AND '.join(where) if where else ''
    with connect(current_app.config['DATABASE']) as c:
        count=c.execute('SELECT COUNT(*) FROM document_references r'+clause,args).fetchone()[0]
        refs=c.execute('SELECT r.* FROM document_references r'+clause+' ORDER BY r.id DESC LIMIT 40 OFFSET ?',args+[(page-1)*40]).fetchall()
    return render_template('document_references.html',references=refs,count=count,page=page,pages=max(1,(count+39)//40),accept=ACCEPT)

@bp.get('/documents/<int:document_id>/download')
def download(document_id):
    with connect(current_app.config['DATABASE']) as c:doc=c.execute('SELECT * FROM documents WHERE id=?',(document_id,)).fetchone()
    if not doc:abort(404)
    root=Path(current_app.config['DOCUMENTS_DIR']).resolve();path=(root/doc['storage_name']).resolve()
    if path.parent!=root or not path.is_file():abort(404)
    response=send_file(path,as_attachment=True,download_name=doc['filename'],mimetype='application/octet-stream')
    response.headers['Cache-Control']='private, no-store'
    return response

@bp.get('/documents/<int:document_id>')
def detail(document_id):
    with connect(current_app.config['DATABASE']) as c:
        doc=c.execute('SELECT * FROM documents WHERE id=?',(document_id,)).fetchone()
        refs=c.execute('SELECT * FROM document_references WHERE document_id=? ORDER BY id DESC',(document_id,)).fetchall()
    if not doc:abort(404)
    return render_template('document_detail.html',doc=doc,references=refs)

@bp.get('/documents/<int:document_id>/preview')
def preview(document_id):
    with connect(current_app.config['DATABASE']) as c:doc=c.execute('SELECT * FROM documents WHERE id=?',(document_id,)).fetchone()
    if not doc or doc['kind']!='Image':abort(404)
    root=Path(current_app.config['DOCUMENTS_DIR']).resolve();path=(root/doc['storage_name']).resolve()
    mime=IMAGE_MIMES.get(path.suffix.lower())
    if not mime or path.parent!=root or not path.is_file():abort(404)
    response=send_file(path,as_attachment=False,download_name=doc['filename'],mimetype=mime)
    response.headers['Cache-Control']='private, no-store'
    return response
