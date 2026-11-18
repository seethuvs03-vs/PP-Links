"""Canonical platform identities; tracking parameters never create new records."""
import re
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode

HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "mbasic.facebook.com", "web.facebook.com", "fb.watch", "fb.me", "instagram.com", "www.instagram.com", "m.instagram.com", "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}

def normalize_url(raw):
    raw = raw.strip().rstrip('.,;!?\"\'>])}')
    if raw.startswith('www.'):
        raw = 'https://' + raw
    p = urlsplit(raw)
    if p.scheme not in ('http', 'https') or p.hostname not in HOSTS or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError('Unsupported platform URL')
    host, path, q = p.hostname, p.path.rstrip('/'), parse_qs(p.query)
    if 'instagram.com' in host:
        host = 'www.instagram.com'
        path = re.sub(r'^/reels/', '/reel/', path)
        query = ''
    elif 'youtube.com' in host or host == 'youtu.be':
        video = (path.lstrip('/') if host == 'youtu.be' else q.get('v', [''])[0])
        if re.match(r'^/(shorts|embed|live)/', path):
            video = path.split('/')[2]
        host = 'www.youtube.com'
        if video:
            path, query = '/watch', urlencode({'v': video})
        else:
            query = urlencode({k:q[k][0] for k in ('list',) if k in q})
    else:
        host = host if host in ('fb.watch','fb.me') else 'www.facebook.com'
        if path in ('/watch', '/video.php') and q.get('v'):
            path, query = '/reel/' + q['v'][0], ''
        else:
            query = urlencode({k:q[k][0] for k in ('id','story_fbid','fbid','set') if k in q})
    return urlunsplit(('https', host, path or '/', query, ''))

def platform(url):
    host = urlsplit(url).hostname or ''
    return 'Instagram' if 'instagram.com' in host else 'YouTube' if 'youtube.com' in host or host == 'youtu.be' else 'Facebook'

URL_RE = re.compile(r'(?:https?://|www\.)[^\s<>\"\u200e\u200f]+', re.I)
