"""Public HTML scraper refactored from notebook scrape_reel.

Reuses OG/Twitter, JSON-LD and platform field patterns; transport has no login,
cookies, browser fingerprint changes or challenge-solving mechanisms.
"""
import json
import re
import time
import socket
import ipaddress
from datetime import datetime, timezone
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
import requests
from bs4 import BeautifulSoup
from .urls import HOSTS, normalize_url, platform, URL_RE

class Unavailable(Exception): pass

def public_url(url):
    p=urlsplit(url)
    if p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password or p.port not in (None,443):
        raise Unavailable('Redirect or resource outside supported public platforms')
    for address in socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise Unavailable('Non-public network address rejected')

class PublicClient:
    def __init__(self, timeout=20, interval=3, retries=2, respect_robots=True):
        self.timeout=timeout; self.interval=interval; self.retries=retries
        self.respect_robots=respect_robots
        self.last=0; self.robots={}

    def request(self,url):
        public_url(url)
        for attempt in range(self.retries+1):
            time.sleep(max(0,self.interval-(time.monotonic()-self.last)))
            self.last=time.monotonic()
            try:
                # Each request is anonymous and never carries login cookies.
                with requests.get(url,timeout=(self.timeout,self.timeout),allow_redirects=False,stream=True,
                                  headers={'User-Agent':'PPLinks/1.0 (public metadata collector)'}) as r:
                    if r.status_code in (401,403,429):
                        raise Unavailable(f'Platform restriction (HTTP {r.status_code}); retry manually later')
                    if r.status_code >= 500:
                        if attempt < self.retries: time.sleep(2**attempt); continue
                    r.raise_for_status()
                    if 300 <= r.status_code < 400:
                        return '',urljoin(url,r.headers.get('Location','')),r.status_code
                    parts=[];size=0;started=time.monotonic()
                    for chunk in r.iter_content(65536):
                        size+=len(chunk)
                        if size>8*1024*1024 or time.monotonic()-started>self.timeout:
                            raise Unavailable('Response exceeds size/time limit')
                        parts.append(chunk)
                    return b''.join(parts).decode(r.encoding if r.encoding and r.encoding!='ISO-8859-1' else 'utf-8',errors='replace'),url,r.status_code
            except (requests.Timeout,requests.ConnectionError):
                if attempt==self.retries: raise
                time.sleep(2**attempt)
        raise Unavailable('Request failed')

    def allowed(self,url):
        if not self.respect_robots:return
        p=urlsplit(url);origin=f'{p.scheme}://{p.netloc}'
        if origin not in self.robots:
            try:
                body,_,code=self.request(origin+'/robots.txt')
                if 300<=code<400: raise Unavailable('Could not verify robots policy')
            except requests.HTTPError as e:
                if e.response.status_code==404: body=''
                else: raise Unavailable('Could not verify robots policy') from e
            rp=RobotFileParser();rp.parse(body.splitlines()); self.robots[origin]=rp
        if not self.robots[origin].can_fetch('PPLinks',url): raise Unavailable('Retrieval disallowed by platform robots policy')

    def get(self,url):
        for _ in range(6):
            public_url(url);self.allowed(url)
            body,target,code=self.request(url)
            if 300<=code<400: url=target;continue
            if any(part in urlsplit(url).path.lower() for part in ('/login','/checkpoint','/challenge','/consent')):
                raise Unavailable('Login or challenge page; retrieval stopped')
            return body,url
        raise Unavailable('Too many redirects')

def json_objects(value):
    if isinstance(value,list):
        for v in value: yield from json_objects(v)
    elif isinstance(value,dict):
        yield value
        if '@graph' in value: yield from json_objects(value['@graph'])

def named(value):
    if isinstance(value,dict): return value.get('name') or value.get('url') or ''
    if isinstance(value,list): return ', '.join(str(named(v)) for v in value)
    return value or ''

def parse_html(src,url):
    soup=BeautifulSoup(src,'html.parser')
    def og(prop):
        tag=soup.find('meta',property=prop) or soup.find('meta',attrs={'name':prop})
        return tag.get('content','').strip() if tag else ''
    title=og('og:title') or og('twitter:title') or (soup.title.get_text(strip=True) if soup.title else '')
    if re.search(r'^(log\s?in|sign in|security check|just a moment|before you continue)',title,re.I) or soup.select('input[type="password"], #captcha, .g-recaptcha'):
        raise Unavailable('Login, CAPTCHA or access challenge; retrieval stopped')
    res=dict(url=url,final_url=url,platform=platform(url),title=title,
             description=og('og:description') or og('twitter:description') or og('description'),
             thumbnail=og('og:image') or og('twitter:image'),author='',duration='',posted_date='',views='',likes='')
    res['media_type']=og('og:type');res['keywords']=og('keywords');res['username']=''
    res['meta_tags']={str(t.get('property') or t.get('name')):t.get('content') for t in soup.find_all('meta') if t.get('content')}
    ld=[]
    for script in soup.find_all('script',type='application/ld+json'):
        try: ld.extend(json_objects(json.loads(script.string or script.get_text())))
        except (ValueError,TypeError): continue
    res['structured_data']=ld
    # Prefer content entities, not unrelated Organization/WebSite information.
    for d in ld:
        if not any(t in str(d.get('@type','')) for t in ('VideoObject','SocialMediaPosting','Article','ImageObject')): continue
        for field,key in [('title','name'),('description','description'),('duration','duration'),('posted_date','uploadDate'),('transcript','transcript'),('caption','caption'),('keywords','keywords')]:
            res[field]=res.get(field) or d.get(key,'')
        res['posted_date']=res['posted_date'] or d.get('datePublished','')
        res['author']=res['author'] or named(d.get('author'))
        res['location']=named(d.get('contentLocation') or d.get('location'))
        res['media_type']=res['media_type'] or d.get('@type','')
        stats=d.get('interactionStatistic',[])
        for stat in stats if isinstance(stats,list) else [stats]:
            if not isinstance(stat,dict):continue
            kind=str(stat.get('interactionType','')).lower()
            for marker,field in [('watch','views'),('view','views'),('like','likes'),('comment','comments'),('share','shares')]:
                if marker in kind:res[field]=stat.get('userInteractionCount','')
    # Patterns retained from notebook, with field-specific duration units.
    patterns={
      'views':[r'"playCount":\s*(\d+)',r'"view_count":\s*(\d+)',r'"video_view_count":\s*(\d+)',r'"viewCount":\s*"?(\d+)'],
      'likes':[r'"reactionCount":\s*(\d+)',r'"like_count":\s*(\d+)',r'"edge_media_preview_like":\s*\{"count":\s*(\d+)'],
      'author':[r'"actor":\s*\{"[^}]*?"name":"([^"]+)"',r'"owner":\s*\{"[^}]*?"name":"([^"]+)"'],
      'username':[r'"owner":\s*\{"[^}]*?"username":"([^"]+)"'],
      'posted_date':[r'"publish_time":\s*(\d+)',r'"creation_time":\s*(\d+)',r'"taken_at_timestamp":\s*(\d+)'],
      'comments':[r'"comment_count":\s*(\d+)'], 'shares':[r'"share_count":\s*(\d+)']}
    # Page-wide regexes can capture the logged-in viewer or another feed item.
    for field,pats in (patterns.items() if res['platform']!='Facebook' else []):
        if res.get(field) not in ('',None): continue
        for pat in pats:
            m=re.search(pat,src)
            if m:
                val=m.group(1)
                if field=='posted_date':
                    try:val=datetime.fromtimestamp(int(val),timezone.utc).isoformat()
                    except (ValueError,OverflowError,OSError):continue
                res[field]=int(val) if field in ('views','likes','comments','shares') else val
                break
    if not res['duration'] and res['platform']!='Facebook':
        for key,divisor in [('playable_duration_in_ms',1000),('length_in_second',1),('video_duration',1)]:
            m=re.search(r'"'+key+r'":\s*([\d.]+)',src)
            if m:res['duration']=f'{float(m.group(1))/divisor:g}s';break
    # Public YouTube player data, when actually present in returned HTML.
    match=re.search(r'(?:var\s+)?ytInitialPlayerResponse\s*=\s*',src)
    if match:
        try:
            player,_=json.JSONDecoder().raw_decode(src[match.end():])
            if player.get('playabilityStatus',{}).get('status') not in (None,'OK'): raise Unavailable('YouTube reports video unavailable or restricted')
            details=player.get('videoDetails',{})
            for field,key in [('title','title'),('description','shortDescription'),('author','author'),('views','viewCount'),('keywords','keywords'),('channel_id','channelId')]:
                if key in details:res[field]=details[key]
            if details.get('lengthSeconds'):res['duration']=details['lengthSeconds']+'s'
            res['caption_tracks']=player.get('captions',{}).get('playerCaptionsTracklistRenderer',{}).get('captionTracks',[])
        except (ValueError,TypeError):pass
    if res['platform']=='Facebook':
        from .facebook_details import extract,generic_title
        if generic_title(res['title']):res['title']=''
        res.update(extract(soup,url))
        if not res.get('title') and not res.get('description'):
            if res.get('source_item_id') and any(res.get(k) for k in ('author','duration','posted_date','thumbnail')):
                res['retrieval_status']='partial'
                res['retrieval_note']='Video metadata retrieved, but Facebook returned no title or caption for this post. Review the original video and add item details manually if needed.'
            else:
                raise Unavailable('Facebook returned no title or caption and no identifiable video metadata for the requested post. Review the original link; another retry may not provide more information.')
    text=' '.join(str(res.get(k) or '') for k in ('title','description','caption','transcript'))
    res['hashtags']=list(dict.fromkeys(re.findall(r'#\w+',text)))
    res['external_links']=list(dict.fromkeys(URL_RE.findall(text)))
    res['page_profile_channel']=res['author'] or res['username']
    if res.get('retrieval_status')!='partial' and not res['description'] and res['title'].strip().lower() in ('','facebook','instagram','youtube','facebook - log in or sign up'):
        raise Unavailable('No item metadata publicly available')
    return res

class Scraper:
    def __init__(self,**kwargs):self.client=PublicClient(**kwargs)
    def fetch_page(self,url):
        return self.client.get(url)
    def __call__(self,url):
        src,final=self.fetch_page(url)
        result=parse_html(src,final)
        # Caption retrieval only uses public, policy-permitted platform URLs.
        tracks=result.get('caption_tracks',[])
        if tracks and not result.get('transcript'):
            try:
                body,_=self.client.get(tracks[0]['baseUrl'])
                if body.lstrip().startswith('{'):
                    data=json.loads(body)
                    transcript=' '.join(seg.get('utf8','') for event in data.get('events',[]) for seg in event.get('segs',[]))
                else:
                    transcript=' '.join(t.get_text(' ',strip=True) for t in BeautifulSoup(body,'html.parser').find_all('text'))
                if transcript:result['transcript']=transcript
                result['transcript_language']=tracks[0].get('languageCode','')
            except Exception as exc:result['transcript_error']=str(exc)[:500]
        result['retrieved_at']=datetime.now(timezone.utc).isoformat()
        result['url']=url
        return result
