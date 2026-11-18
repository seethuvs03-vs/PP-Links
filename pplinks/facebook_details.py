"""Read only JSON entities belonging to the requested Facebook video."""
import json
import re
from datetime import datetime,timezone
from urllib.parse import urlsplit,parse_qs

def generic_title(value):
    return bool(re.fullmatch(r'\s*(?:\(\d+\)\s*)?(?:Facebook|Reels?|Watch)?\s*',value or '',re.I))

def objects(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from objects(child)
    elif isinstance(value,list):
        for child in value:yield from objects(child)

def extract(soup,url):
    p=urlsplit(url)
    match=re.search(r'/(?:reel|videos)/(\d+)',p.path)
    target=match.group(1) if match else parse_qs(p.query).get('v',[''])[0]
    if not target:return {}
    nodes=[]
    for script in soup.find_all('script',type='application/json'):
        try:nodes.extend(objects(json.loads(script.string or script.get_text())))
        except (ValueError,TypeError,RecursionError):continue
    media=[n for n in nodes if str(n.get('id'))==target]
    stories=[]
    for node in nodes:
        attachments=node.get('attachments')
        if isinstance(attachments,list) and any(isinstance(a,dict) and isinstance(a.get('media'),dict) and str(a['media'].get('id'))==target for a in attachments):
            stories.append(node)
    # Deferred fragments may carry only the story ID, with the attachment in
    # an earlier fragment. Join on that exact ID, never on page-wide proximity.
    story_ids={str(n['id']) for n in stories if n.get('id')}
    for node in media:
        story=node.get('creation_story')
        if isinstance(story,dict):
            stories.append(story)
            if story.get('id'):story_ids.add(str(story['id']))
    stories.extend(n for n in nodes if n.get('id') and str(n['id']) in story_ids)
    result={}
    def text(v):return v.get('text','') if isinstance(v,dict) else v if isinstance(v,str) else ''
    for node in stories:
        message=text(node.get('message'))
        if message:
            result['description']=message;result['caption']=message
        actors=node.get('actors') or []
        if actors and isinstance(actors[0],dict) and actors[0].get('name'):
            result['author']=actors[0]['name']
            result['profile_url']=actors[0].get('url','')
        stamp=node.get('creation_time')
        if isinstance(stamp,(int,float)):
            try:result['posted_date']=datetime.fromtimestamp(stamp,timezone.utc).isoformat()
            except (ValueError,OverflowError,OSError):pass
        feedback=node.get('feedback') or {}
        if isinstance(feedback,dict):
            for source,dest in [('reaction_count','reactions'),('share_count','shares'),('comment_count','comments'),('video_view_count','views')]:
                v=feedback.get(source)
                if isinstance(v,dict):v=v.get('count')
                if isinstance(v,(int,float)):result[dest]=v
    for node in media:
        owner=node.get('owner')
        if isinstance(owner,dict) and owner.get('name'):
            result['author']=owner['name'];result['profile_url']=owner.get('url','')
        for key in ('title','name'):
            value=text(node.get(key))
            if value and not generic_title(value):result['title']=value
        duration=node.get('length_in_second')
        if isinstance(duration,(int,float)):result['duration']=f'{duration:g}s'
        thumbnail=node.get('first_frame_thumbnail')
        if isinstance(thumbnail,str):result['thumbnail']=thumbnail
    if result.get('caption') and not result.get('title'):
        # A literal excerpt, identified as such; never invent a product name.
        result['title']=result['caption'].splitlines()[0][:200]
        result['title_source']='Caption excerpt'
    if result:
        result['facebook_parser_version']=2
        result['source_item_id']=target
        result['media_type']='Video'
    return result
