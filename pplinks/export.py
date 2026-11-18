"""Reuse notebook writer; preserve original 13-column output and add detail sheets."""
import io
import json
import re
from copy import copy
import openpyxl
from openpyxl.styles import Font,PatternFill,Alignment
from .legacy_excel import write_excel
from .db import connect
from .details import effective_details

HEADERS=['#','Original URL','Final URL','Title','Author','Description','Views','Likes','Duration','Posted Date','Thumbnail URL','Status','Error']
KEYS=['url','final_url','title','author','description','views','likes','duration','posted_date','thumbnail','status','error']

def safe(value):
    if isinstance(value,(dict,list)):value=json.dumps(value,ensure_ascii=False)
    if isinstance(value,str):
        value=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',value)[:32767]
        if value.startswith(('=','+','-','@','\t','\r')):value="'"+value
    return value

def detail_sheet(wb,title,headers,rows):
    ws=wb.create_sheet(title);ws.append(headers)
    for row in rows:ws.append([safe(v) for v in row])
    for c in ws[1]:
        c.font=Font(name='Arial',bold=True,color='FFFFFF');c.fill=PatternFill('solid',fgColor='1877F2')
    for col in ws.columns:ws.column_dimensions[col[0].column_letter].width=28
    for row in ws.iter_rows(min_row=2):
        for cell in row:cell.alignment=Alignment(vertical='top',wrap_text=True)
    ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
    return ws

def export_excel(db):
    with connect(db) as c:
        items=[dict(r) for r in c.execute('SELECT * FROM items ORDER BY id')]
        occurrences=[dict(r) for r in c.execute('SELECT * FROM occurrences ORDER BY id')]
        jobs=[dict(r) for r in c.execute('SELECT * FROM jobs ORDER BY id')]
        legacy=[dict(r) for r in c.execute('SELECT * FROM legacy_rows ORDER BY id')]
    records=[]
    for item in items:
        meta=effective_details(json.loads(item['metadata']));item['details']=meta
        records.append({**{k:safe(v) for k,v in meta.items()},'url':item['original_url'],
                        'status':'MANUAL' if meta.get('manual_details') else {'ok':'OK','partial':'PARTIAL','failed':'ERROR','pending':'PENDING','processing':'PROCESSING'}[item['status']], 'error':safe(item['error'])})
    buf=io.BytesIO();write_excel(records,buf);buf.seek(0);wb=openpyxl.load_workbook(buf)
    ws=wb['Facebook Reels']
    # Keep observed workbook widths, header style, row colors and hyperlinks.
    ws.column_dimensions['B'].width=48.140625
    ws.freeze_panes='A2';ws.auto_filter.ref=f'A1:M{max(1,len(records)+1)}'
    wb['Summary']['B2']='pplinks-master.xlsx'
    wb['Summary']['B6']=sum(i['status']=='failed' for i in items)
    wb['Summary']['B5']=sum(i['status']=='ok' for i in items)
    wb['Summary'].append(['Pending / processing',sum(i['status'] in ('pending','processing') for i in items)])
    wb['Summary'].append(['Partial / needs details',sum(i['status']=='partial' for i in items)])
    fields=['platform','page_profile_channel','username','title','caption','description','transcript','hashtags','posted_date','views','likes','comments','shares','media_type','external_links','location','channel_id','transcript_language','transcript_error','retrieved_at']
    wb['Summary'].append(['User-entered details',sum(bool(i['details'].get('manual_details')) for i in items)])
    fields+=['details_source','manual_fields','manual_updated_at','title_source','source_item_id','reactions']
    detail_sheet(wb,'Item Details',['Record ID','Original URL','Category','Subcategory','Keywords','Confidence','Reason','Manual category']+fields,
                 ([i['id'],i['original_url'],i['category'],i['subcategory'],json.loads(i['keywords']),i['confidence'],i['reason'],bool(i['manual_category'])]+[i['platform'] if f=='platform' else i['details'].get(f) for f in fields] for i in items))
    detail_sheet(wb,'WhatsApp Messages',['Occurrence ID','Job ID','Record ID','Sender','Sent at (local, no timezone)','Original timestamp','Original message','Original URL'],
                 ([o[k] for k in ['id','job_id','item_id','sender','sent_at','timestamp_raw','message','original_url']] for o in occurrences))
    keys=['id','filename','created_at','kind','status','total','new','existing','updated','failed','processed','error']
    detail_sheet(wb,'Processing History',keys,([j[k] for k in keys] for j in jobs))
    if legacy:
        detail_sheet(wb,'Legacy Import Audit',['Source workbook','Source row','Record ID','Import note']+HEADERS,
                     ([r['source'].split('#')[0],r['row_number'],r['item_id'],r['note']]+json.loads(r['raw_json']) for r in legacy))
    output=io.BytesIO();wb.save(output);output.seek(0);return output
