import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import openpyxl
import pytest
from pplinks import create_app,ROOT
from pplinks.db import connect,init_db
from pplinks.jobs import submit_upload,Worker,retry_failed
from pplinks.whatsapp import parse_export,decode_upload
from pplinks.urls import normalize_url
from pplinks.scraper import parse_html,Unavailable,PublicClient,public_url
from pplinks.export import export_excel,HEADERS
from pplinks.categories import categorize,load_rules

@pytest.fixture
def app(tmp_path):
    categories=tmp_path/'categories.json'
    categories.write_text(json.dumps({'fallback':'Uncategorized','rules':[{'category':'Home & Construction','subcategory':'Walls & Fencing','keywords':['precast','fence','fencing','മതിൽ']}]}),encoding='utf-8')
    return create_app({'TESTING':True,'DATABASE':str(tmp_path/'test.sqlite3'),'CATEGORIES':str(categories),'SECRET_KEY':'test','PURCHASE_MASTER_PATH':str(tmp_path/'purchase.xlsx')},start_worker=False)

def rows(app,sql,args=()):
    with connect(app.config['DATABASE']) as c:return [dict(r) for r in c.execute(sql,args)]

def fake(url):return {'title':'Precast fencing','description':'Public product details','final_url':url,'views':0}

def worker(app,scraper=fake):return Worker(app.config['DATABASE'],app.config['CATEGORIES'],scraper)

def submit(app,text):return submit_upload(app.config['DATABASE'],'chat.txt',parse_export(text))

def token(client):
    client.get('/')
    with client.session_transaction() as s:return s['csrf']

def test_partial_metadata_saved_exported_and_retryable(app):
    job=submit(app,'https://www.facebook.com/reel/123456')
    def partial(url):
        return {'final_url':url,'author':'Verified creator','duration':'12s','retrieval_status':'partial','retrieval_note':'Caption unavailable'}
    worker(app,partial).process_job(job)
    item=rows(app,'SELECT * FROM items')[0]
    assert item['status']=='partial' and item['error']=='Caption unavailable'
    book=openpyxl.load_workbook(export_excel(app.config['DATABASE']))
    sheet=book['Facebook Reels']
    assert sheet['E2'].value=='Verified creator'
    assert not sheet['D2'].value and not sheet['F2'].value
    assert sheet['L2'].value=='PARTIAL'
    client=app.test_client()
    assert b'Needs details' in client.get('/results?status=partial').data
    assert client.get('/items/'+str(item['id'])).status_code==200
    again=retry_failed(app.config['DATABASE'],item['id'])
    worker(app).process_job(again)
    updated=rows(app,'SELECT * FROM items')[0]
    assert updated['status']=='ok' and updated['error']==''
    assert 'retrieval_status' not in json.loads(updated['metadata'])

def test_paste_links_dedup_scrape_and_purchase_file(app):
    client=app.test_client();csrf=token(client)
    url='https://www.facebook.com/reel/123456789'
    assert client.post('/paste-links',data={'links':url}).status_code==400
    assert client.post('/paste-links',data={'csrf':csrf,'links':url+'\n'+url}).status_code==302
    saved=rows(app,'SELECT * FROM jobs ORDER BY id DESC')[0]
    assert saved['filename']=='Pasted links' and saved['new']==1 and saved['existing']==1
    w=app.extensions['worker'];w.scraper=fake;w.process_job(saved['id'])
    assert rows(app,'SELECT status FROM items')[0]['status']=='ok'
    book=openpyxl.load_workbook(app.config['PURCHASE_MASTER_PATH'])
    assert book['Facebook Reels']['D2'].value=='Precast fencing'
    client.post('/paste-links',data={'csrf':csrf,'links':url})
    assert len(rows(app,'SELECT * FROM items'))==1
    assert rows(app,'SELECT * FROM jobs ORDER BY id DESC')[0]['new']==0
    for raw in ('','https://example.com/test','x'*100001):
        assert client.post('/paste-links',data={'csrf':csrf,'links':raw}).status_code==400

@pytest.mark.parametrize('source,target',[
    ('https://youtu.be/abc123?t=10','https://www.youtube.com/watch?v=abc123'),
    ('https://m.youtube.com/shorts/abc123?si=a','https://www.youtube.com/watch?v=abc123'),
    ('https://www.youtube.com/watch?v=abc123&list=PL123','https://www.youtube.com/watch?v=abc123'),
    ('https://www.instagram.com/reels/ABC/?igsh=a','https://www.instagram.com/reel/ABC'),
    ('https://m.facebook.com/watch/?v=123&mibextid=x','https://www.facebook.com/reel/123'),
    ('https://facebook.com/story.php?story_fbid=123&id=456&foo=bar','https://www.facebook.com/story.php?id=456&story_fbid=123'),
])
def test_url_aliases(source,target):assert normalize_url(source)==target

@pytest.mark.parametrize('url',['https://facebook.com.evil.com/reel/1','http://localhost/a','https://user@facebook.com/reel/1','https://instagram.com:999/a','file:///etc/passwd'])
def test_reject_urls(url):
    with pytest.raises(ValueError):normalize_url(url)

def test_multiline_whatsapp():
    text='[13/09/2026, 9:10:11 PM] Alice: മതിൽ\nhttps://instagram.com/p/ABC/?igsh=1\n13/09/2026, 21:12 - Bob: https://youtu.be/123 https://youtu.be/123?t=4'
    items=parse_export(text)
    assert len(items)==3 and items[0]['sender']=='Alice'
    assert items[0]['sent_at']=='2026-09-13T21:10:11'
    assert 'മതിൽ' in items[0]['message'] and items[1]['sender']=='Bob'
    assert parse_export('9/13/26, 9:10 PM - A: https://youtu.be/abc','MDY')[0]['sent_at']=='2026-09-13T21:10:00'
    assert decode_upload(text.encode('utf-16'))==text
    with pytest.raises(ValueError):decode_upload(b'abc\x00')

def test_recurring_uploads_and_persistence(app):
    first='\n'.join('https://instagram.com/p/A'+str(i) for i in range(100))
    j=submit(app,first);w=worker(app);w.process_job(j)
    second='\n'.join('https://instagram.com/p/A'+str(i) for i in range(30,130))
    j2=submit(app,second);w.process_job(j2)
    job=rows(app,'SELECT * FROM jobs WHERE id=?',(j2,))[0]
    assert (job['total'],job['new'],job['existing'],job['processed'])==(100,30,70,30)
    assert len(rows(app,'SELECT * FROM items'))==130
    assert len(rows(app,'SELECT * FROM occurrences'))==200
    init_db(app.config['DATABASE'])
    assert len(rows(app,'SELECT * FROM items'))==130

def test_concurrent_upload_reservations(app):
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:submit(app,'https://youtu.be/abc'),range(8)))
    assert len(rows(app,'SELECT * FROM items'))==1
    assert sum(j['new'] for j in rows(app,'SELECT * FROM jobs'))==1

def test_failure_isolation_retry_and_manual_category(app):
    def sometimes(url):
        if url.endswith('bad'):raise Unavailable('Private content')
        return fake(url)
    j=submit(app,'https://youtu.be/good\nhttps://youtu.be/bad');worker(app,sometimes).process_job(j)
    failed=rows(app,"SELECT * FROM items WHERE status='failed'")[0]
    assert rows(app,'SELECT * FROM jobs')[0]['failed']==1
    client=app.test_client();csrf=token(client)
    assert client.post(f"/items/{failed['id']}/category",data={'csrf':csrf,'category':'My category','subcategory':'Notes'}).status_code==302
    retry=retry_failed(app.config['DATABASE']);worker(app).process_job(retry)
    item=rows(app,'SELECT * FROM items WHERE id=?',(failed['id'],))[0]
    assert item['status']=='ok' and item['category']=='My category' and item['attempts']==2
    assert rows(app,'SELECT * FROM jobs WHERE id=?',(retry,))[0]['updated']==1
    assert rows(app,'SELECT * FROM jobs WHERE id=?',(j,))[0]['failed']==1

def test_redirect_merge_preserves_occurrences(app):
    j=submit(app,'https://facebook.com/reel/123');worker(app).process_job(j)
    j2=submit(app,'https://facebook.com/share/r/ABC')
    worker(app,lambda url:{**fake(url),'final_url':'https://www.facebook.com/reel/123'}).process_job(j2)
    assert len(rows(app,'SELECT * FROM items'))==1
    assert len(rows(app,'SELECT * FROM occurrences'))==2
    assert len(rows(app,'SELECT * FROM aliases'))==2
    job=rows(app,'SELECT * FROM jobs WHERE id=?',(j2,))[0]
    assert job['new']==0 and job['existing']==1
    assert submit(app,'https://facebook.com/share/r/ABC')
    assert len(rows(app,'SELECT * FROM items'))==1

def test_redirect_merge_into_later_queued_item(app):
    job=submit(app,'https://facebook.com/share/r/ABC\nhttps://facebook.com/reel/123')
    calls=[]
    def scrape(url):
        calls.append(url);return {**fake(url),'final_url':'https://www.facebook.com/reel/123'}
    worker(app,scrape).process_job(job)
    result=rows(app,'SELECT * FROM jobs WHERE id=?',(job,))[0]
    assert result['processed']==result['work_total']==2
    assert result['new']==1 and result['existing']==1
    assert len(calls)==1 and len(rows(app,'SELECT * FROM items'))==1
    assert len(rows(app,'SELECT * FROM occurrences'))==2
    assert len(rows(app,'SELECT * FROM aliases'))==2

def test_html_metadata_and_units():
    src='''<title>Fence video</title><meta property="og:description" content="A precast wall #home"><script type="application/ld+json">{"@graph":[{"@type":"VideoObject","author":[{"name":"Maker"}],"uploadDate":"2026-01-01","transcript":"hello","interactionStatistic":{"interactionType":"https://schema.org/LikeAction","userInteractionCount":0}}]}</script><script>{"playable_duration_in_ms":500,"video_view_count":0}</script>'''
    meta=parse_html(src,'https://www.instagram.com/reel/abc')
    assert meta['duration']=='0.5s' and meta['views']==0 and meta['likes']==0
    assert meta['author']=='Maker' and meta['transcript']=='hello' and meta['hashtags']==['#home']
    assert 'location' in meta

def test_youtube_metadata():
    player={'playabilityStatus':{'status':'OK'},'videoDetails':{'title':'Example','shortDescription':'Description','author':'Channel','lengthSeconds':'1500','viewCount':'23'}}
    result=parse_html('<script>var ytInitialPlayerResponse = '+json.dumps(player)+';</script>','https://www.youtube.com/watch?v=abc')
    assert result['duration']=='1500s' and result['author']=='Channel'

@pytest.mark.parametrize('html',['<title>Log in to Facebook</title>','<title>Instagram</title>','<title>Content</title><input type="password">'])
def test_access_wall_is_not_success(html):
    with pytest.raises(Unavailable):parse_html(html,'https://www.facebook.com/reel/123')

def test_category_evidence(app):
    config=load_rules(app.config['CATEGORIES'])
    assert categorize({'description':'precast മതിൽ'},config)['subcategory']=='Walls & Fencing'
    assert categorize({},config)['category']=='Uncategorized'
    assert categorize({'description':'surprice'},config)['category']=='Uncategorized'

def test_web_forms_filters_export(app):
    client=app.test_client();csrf=token(client)
    assert client.post('/upload',data={'file':(io.BytesIO(b'x'),'bad.txt')}).status_code==400
    assert client.post('/upload',data={'csrf':csrf,'file':(io.BytesIO(b'x'),'bad.exe')}).status_code==400
    response=client.post('/upload',data={'csrf':csrf,'file':(io.BytesIO(b'13/09/2026, 10:00 - Bob: https://youtu.be/abc'),'chat.txt')})
    assert response.status_code==302
    worker(app).process_job(1)
    for path in ['/','/results','/results?q=Precast','/results?status=failed','/items/1','/history','/categories','/api/jobs','/items/1/metadata.json','/health']:
        r=client.get(path);assert r.status_code==200,(path,r.data[:100])
    assert client.get('/export.xlsx').status_code==200
    assert client.get('/',headers={'Host':'evil.example'}).status_code==400

def test_excel_compatible_and_formula_safe(app):
    job=submit(app,'https://youtu.be/abc');worker(app,lambda u:{**fake(u),'title':'=HYPERLINK("evil")','description':'മതിൽ'}).process_job(job)
    wb=openpyxl.load_workbook(export_excel(app.config['DATABASE']))
    ws=wb['Facebook Reels']
    assert [c.value for c in ws[1]]==HEADERS
    assert ws['D2'].data_type=='s' and ws['D2'].value.startswith("'=")
    assert ws['G2'].value==0 and ws['F2'].value=='മതിൽ'
    assert ws['B2'].hyperlink and ws.freeze_panes=='A2'
    assert ws['A1'].fill.fgColor.rgb.endswith('1877F2')
    assert wb['Summary']['B4'].value==1 and wb['Summary']['B5'].value==1

def test_import_idempotent_audit_and_aliases(app,tmp_path):
    from pplinks.importer import import_workbook
    wb=openpyxl.Workbook();ws=wb.active;ws.title='Facebook Reels';ws.append(HEADERS)
    ws.append([1,'https://facebook.com/share/r/A','https://facebook.com/reel/123','Title',None,'Description',None,None,None,None,None,'OK',None])
    ws.append(HEADERS)
    ws.append([2,'https://facebook.com/reel/123',None,'Title'])
    ws.append([None,'No link note'])
    path=tmp_path/'legacy.xlsx';wb.save(path)
    result=import_workbook(app.config['DATABASE'],path,app.config['CATEGORIES'])
    assert result['new']==1 and result['existing']==1
    assert len(rows(app,'SELECT * FROM legacy_rows'))==4
    assert import_workbook(app.config['DATABASE'],path,app.config['CATEGORIES'])['already_imported']

def test_robots_and_redirect_guard(monkeypatch):
    monkeypatch.setattr('pplinks.scraper.public_url',lambda u:None)
    client=PublicClient(interval=0)
    client.request=lambda u:('User-agent: *\nDisallow: /reel/',u,200)
    with pytest.raises(Unavailable,match='robots'):client.get('https://www.facebook.com/reel/123')

def test_private_dns_guard(monkeypatch):
    monkeypatch.setattr('pplinks.scraper.socket.getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(Unavailable,match='Non-public'):public_url('https://www.facebook.com/reel/1')

def test_resume_interrupted_job(app):
    job=submit(app,'https://youtu.be/abc')
    with connect(app.config['DATABASE']) as c:
        c.execute("UPDATE jobs SET status='running'")
        c.execute("UPDATE items SET status='processing'")
        c.execute("UPDATE job_items SET state='processing'")
    w=worker(app);w.start()
    import time
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        if rows(app,'SELECT status FROM jobs')[0]['status']=='completed':break
        time.sleep(.03)
    w.stop();w.thread.join(timeout=3)
    assert rows(app,'SELECT status FROM jobs')[0]['status']=='completed'
    assert rows(app,'SELECT attempts FROM items')[0]['attempts']==1

def test_manual_details_export_without_fake_scrape_success(app):
    job=submit(app,'https://facebook.com/share/r/Test')
    def blocked(url):raise Unavailable('Retrieval disallowed by platform robots policy')
    worker(app,blocked).process_job(job)
    client=app.test_client();csrf=token(client)
    response=client.post('/items/1/details',data={'csrf':csrf,'title':'Precast fence','description':'Verified product description','author':'Maker'})
    assert response.status_code==302
    item=rows(app,'SELECT * FROM items')[0]
    assert item['status']=='failed' and item['error']
    metadata=json.loads(item['metadata'])
    assert metadata['manual_details']['title']=='Precast fence' and not metadata.get('title')
    assert item['category']=='Home & Construction'
    assert len(rows(app,'SELECT * FROM detail_edits'))==1
    assert b'Precast fence' in client.get('/results?q=Precast').data
    wb=openpyxl.load_workbook(export_excel(app.config['DATABASE']))
    assert wb['Facebook Reels']['D2'].value=='Precast fence'
    assert wb['Facebook Reels']['L2'].value=='MANUAL'
    assert wb['Summary']['B5'].value==0
    retry=retry_failed(app.config['DATABASE']);worker(app).process_job(retry)
    wb=openpyxl.load_workbook(export_excel(app.config['DATABASE']))
    assert wb['Facebook Reels']['D2'].value=='Precast fence'

def test_manual_details_validation_and_preview_is_opt_in(app):
    submit(app,'https://facebook.com/share/r/Test')
    client=app.test_client();csrf=token(client)
    assert client.post('/items/1/details',data={'title':'test'}).status_code==400
    assert client.post('/items/1/details',data={'csrf':csrf,'title':''}).status_code==400
    response=client.get('/items/1')
    assert b'data-preview-src=' in response.data and b'<iframe hidden' in response.data
    assert b'Save details to collection' in response.data

def test_reupload_explains_existing_unavailable_records(app):
    job=submit(app,'https://facebook.com/share/r/Test')
    def blocked(url):raise Unavailable('Retrieval disallowed by platform robots policy')
    worker(app,blocked).process_job(job)
    client=app.test_client();csrf=token(client)
    response=client.post('/upload',data={'csrf':csrf,'file':(io.BytesIO(b'https://facebook.com/share/r/Test'),'again.txt')},follow_redirects=True)
    assert b'1 previously saved links' in response.data
    assert len(rows(app,'SELECT * FROM items'))==1
