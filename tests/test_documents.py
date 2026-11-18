import io
import sqlite3
import zipfile
import base64
from pathlib import Path
import pytest
from pplinks import create_app
from pplinks.documents import attachment_references

PDF=b'%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF'
CHAT='08/11/2026, 09:10 - Alice: quote.pdf (file attached)\n08/11/2026, 09:11 - Bob: <attached: photo.png>\n08/11/2026, 09:12 - Alice: https://www.facebook.com/reel/123'

@pytest.fixture
def env(tmp_path):
    app=create_app({'TESTING':True,'DATABASE':str(tmp_path/'test.sqlite3'),
        'DOCUMENTS_DIR':str(tmp_path/'files'),'SECRET_KEY':'test','PURCHASE_MASTER_PATH':str(tmp_path/'purchase.xlsx')},start_worker=False)
    client=app.test_client();client.get('/')
    with client.session_transaction() as s:csrf=s['csrf']
    return app,client,csrf

def rows(app,sql):
    with sqlite3.connect(app.config['DATABASE']) as c:return c.execute(sql).fetchall()

def test_chat_document_mode_saves_files_and_provenance_without_scraping(env):
    app,c,t=env
    r=c.post('/upload',data={'csrf':t,'mode':'documents','file':(io.BytesIO(CHAT.encode()),'chat.txt'),
        'attachments':(io.BytesIO(PDF),'quote.pdf')})
    assert r.status_code==302 and r.location.endswith('/documents')
    assert rows(app,'SELECT COUNT(*) FROM items')==[(0,)]
    assert rows(app,'SELECT COUNT(*) FROM jobs')==[(0,)]
    refs=rows(app,'SELECT filename,sender,sent_at,document_id FROM document_references ORDER BY id')
    assert refs[0]==('quote.pdf','Alice','2026-11-08T09:10:00',1)
    assert refs[1][0]=='photo.png' and refs[1][3] is None
    assert c.get('/documents/1/download').data==PDF
    assert 'attachment;' in c.get('/documents/1/download').headers['Content-Disposition']
    assert b'photo.png' in c.get('/documents/references').data

def test_repeat_chat_and_renamed_duplicate_dedup_bytes(env):
    app,c,t=env
    for name in ['quote.pdf','quote.pdf','renamed.pdf']:
        assert c.post('/documents/upload',data={'csrf':t,'chat':(io.BytesIO(CHAT.encode()),'chat.txt'),
            'attachments':(io.BytesIO(PDF),name)}).status_code==302
    assert rows(app,'SELECT COUNT(*) FROM documents')==[(1,)]
    assert rows(app,'SELECT COUNT(*) FROM document_references')==[(2,)]
    assert len(list(Path(app.config['DOCUMENTS_DIR']).iterdir()))==1
    assert b'quote.pdf' in c.get('/documents?q=renamed').data

def test_missing_reference_can_be_uploaded_later_and_persists(env):
    app,c,t=env
    c.post('/documents/upload',data={'csrf':t,'chat':(io.BytesIO(CHAT.encode()),'chat.txt')})
    assert c.post('/documents/upload',data={'csrf':t,'reference_id':'1','attachments':(io.BytesIO(PDF),'renamed.pdf')}).status_code==302
    assert rows(app,'SELECT document_id FROM document_references WHERE id=1')==[(1,)]
    other=create_app(dict(app.config),start_worker=False).test_client()
    assert other.get('/documents/1/download').data==PDF
    assert b'Alice' in other.get('/documents/references?status=all').data

def test_invalid_batch_is_atomic_and_csrf_protected(env):
    app,c,t=env
    assert c.post('/documents/upload',data={'attachments':(io.BytesIO(PDF),'x.pdf')}).status_code==400
    r=c.post('/documents/upload',data={'csrf':t,'attachments':[(io.BytesIO(PDF),'good.pdf'),(io.BytesIO(b'<script>bad</script>'),'fake.pdf')]})
    assert r.status_code==400
    assert rows(app,'SELECT COUNT(*) FROM documents')==[(0,)]
    assert not Path(app.config['DOCUMENTS_DIR']).exists()

def test_limits_and_traversal_names(env):
    app,c,t=env;app.config['DOCUMENT_MAX_FILE_MB']=1
    assert c.post('/documents/upload',data={'csrf':t,'attachments':(io.BytesIO(b'%PDF-'+b'x'*1048576),'big.pdf')}).status_code==400
    assert c.post('/documents/upload',data={'csrf':t,'attachments':(io.BytesIO(PDF),'../../outside.pdf')}).status_code==302
    assert rows(app,'SELECT filename FROM documents')==[('outside.pdf',)]
    assert not (Path(app.config['DOCUMENTS_DIR']).parent/'outside.pdf').exists()
    assert c.get('/documents/999/download').status_code==404

def test_reference_only_and_media_omitted_do_not_invent_files(env):
    app,c,t=env
    r=c.post('/upload',data={'csrf':t,'mode':'documents','file':(io.BytesIO(b'08/11/2026, 09:00 - Alice: <Media omitted>'),'chat.txt')})
    assert r.status_code==400 and b'actual files' in r.data
    assert rows(app,'SELECT COUNT(*) FROM documents')==[(0,)]

def test_document_mode_does_not_change_default_link_upload(env):
    app,c,t=env
    assert c.post('/upload',data={'csrf':t,'file':(io.BytesIO(CHAT.encode()),'chat.txt')}).status_code==302
    assert rows(app,'SELECT COUNT(*) FROM items')==[(1,)]
    assert rows(app,'SELECT COUNT(*) FROM documents')==[(0,)]

def test_different_contents_same_filename_are_not_conflated(env):
    app,c,t=env
    c.post('/documents/upload',data={'csrf':t,'chat':(io.BytesIO(CHAT.encode()),'chat.txt'),
        'attachments':[(io.BytesIO(PDF),'quote.pdf'),(io.BytesIO(PDF+b'\n%version 2'),'quote.pdf')]})
    assert rows(app,'SELECT COUNT(*) FROM documents')==[(2,)]
    assert rows(app,'SELECT document_id FROM document_references WHERE filename="quote.pdf"')==[(None,)]

def test_excel_word_and_image_upload_and_download(env):
    import openpyxl
    app,c,t=env
    excel=io.BytesIO();book=openpyxl.Workbook();book.active['A1']='Purchase item';book.save(excel)
    word=io.BytesIO()
    with zipfile.ZipFile(word,'w') as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8NsAAAAASUVORK5CYII=')
    payloads=[('purchase.xlsx',excel.getvalue()),('notes.docx',word.getvalue()),('photo.png',png)]
    response=c.post('/documents/upload',data={'csrf':t,'attachments':[(io.BytesIO(data),name) for name,data in payloads]})
    assert response.status_code==302
    assert rows(app,'SELECT kind FROM documents ORDER BY id')==[('Excel',),('Word',),('Image',)]
    for i,(_,data) in enumerate(payloads,1):assert c.get(f'/documents/{i}/download').data==data
    assert b'purchase.xlsx' not in c.get('/documents?kind=Image').data

def test_upload_batch_size_limit(env):
    app,c,t=env;app.config['DOCUMENT_MAX_BATCH_MB']=1
    assert c.post('/documents/upload',data={'csrf':t,'attachments':(io.BytesIO(PDF+b'x'*1048576),'big.pdf')}).status_code==413

def test_large_image_gallery_details_and_inline_preview(env):
    app,c,t=env
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8NsAAAAASUVORK5CYII=')
    c.post('/documents/upload',data={'csrf':t,'chat':(io.BytesIO(CHAT.encode()),'chat.txt'),'attachments':(io.BytesIO(png),'photo.png')})
    gallery=c.get('/documents?kind=Image')
    assert gallery.status_code==200 and b'View large image' in gallery.data and b'/documents/1/preview' in gallery.data
    listing=c.get('/documents?view=list')
    assert listing.status_code==200 and b'photo.png' in listing.data and b'document-gallery' not in listing.data
    detail=c.get('/documents/1')
    assert detail.status_code==200 and b'document-large-image' in detail.data and b'Bob' in detail.data
    preview=c.get('/documents/1/preview')
    assert preview.data==png and preview.mimetype=='image/png'
    assert preview.headers['Content-Disposition'].startswith('inline;')
    assert preview.headers['X-Content-Type-Options']=='nosniff'
    assert c.get('/documents/1/download').headers['Content-Disposition'].startswith('attachment;')
    c.post('/documents/upload',data={'csrf':t,'attachments':(io.BytesIO(PDF),'quote.pdf')})
    assert c.get('/documents/2/preview').status_code==404
    assert c.get('/documents/2').status_code==200
    assert c.get('/documents/999/preview').status_code==404

def test_overview_txt_upload_has_no_processing_choice(env):
    _,c,_=env
    html=c.get('/').data
    assert b'upload-mode' not in html and b'chat-attachments' not in html
    assert b'Upload &amp; scrape' in html or b'Upload & scrape' in html
    assert b'href="/documents"' in html
