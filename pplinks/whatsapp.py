"""Android/iOS exports, multiline messages, Unicode and unstructured link lists."""
import re
from datetime import datetime
from .urls import URL_RE, normalize_url

HEADER = re.compile(r'^\[?(\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap][Mm])?)\]?\s*(?:-\s*)?(.*)$')

def timestamp(date, time, order):
    date = re.sub(r'[.-]', '/', date)
    for df in (['%Y/%m/%d'] if len(date.split('/')[0]) == 4 else ([ '%m/%d/%Y','%m/%d/%y'] if order == 'MDY' else ['%d/%m/%Y','%d/%m/%y'])):
        for tf in ['%H:%M','%H:%M:%S','%I:%M %p','%I:%M:%S %p']:
            try:
                return datetime.strptime(date+' '+time.strip().upper(), df+' '+tf).isoformat()
            except ValueError:
                pass
    return None

def parse_messages(text, date_order='DMY'):
    messages, current = [], None
    for line in text.replace('\u200e','').replace('\u200f','').replace('\u202f',' ').splitlines():
        m = HEADER.match(line)
        if m:
            if current: messages.append(current)
            date, time, body = m.groups()
            sender, sep, body2 = body.partition(': ')
            current = dict(sender=sender if sep else '', sent_at=timestamp(date,time,date_order), timestamp_raw=date+' '+time, message=body2 if sep else body)
        else:
            if current is None: current = dict(sender='',sent_at=None,timestamp_raw='',message='')
            current['message'] += ('\n' if current['message'] else '') + line
    if current: messages.append(current)
    return messages

def parse_export(text, date_order='DMY'):
    messages=parse_messages(text,date_order)
    result=[]
    for msg in messages:
        for match in URL_RE.finditer(msg['message']):
            try: url=normalize_url(match.group())
            except ValueError: continue
            result.append(dict(msg, url=url, original_url=match.group().rstrip('.,;!?\"\'>])}')))
    return result

def decode_upload(raw):
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')): text=raw.decode('utf-16')
    else: text=raw.decode('utf-8-sig')
    if '\x00' in text or any(ord(c)<9 for c in text): raise ValueError('The file is not a plain-text export')
    return text
