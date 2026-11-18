import json
import pytest
from pplinks.scraper import parse_html,Unavailable

URL='https://www.facebook.com/reel/123456'
def page(nodes):return '<title>(2) Facebook</title><script type="application/json">'+json.dumps(nodes)+'</script>'

def test_matches_target_caption_and_author_not_viewer_or_other_reel():
    nodes=[{'actor':{'name':'Signed-in viewer'}},
        {'attachments':[{'media':{'id':'999','owner':{'name':'Other creator'}}}],'message':{'text':'Wrong post'}},
        {'attachments':[{'media':{'id':'123456','owner':{'name':'Correct creator'},'length_in_second':15.5}}],
         'actors':[{'name':'Correct creator'}],'message':{'text':'Silicon sealant #waterproof'},'creation_time':1787627991}]
    result=parse_html(page(nodes),URL)
    assert result['author']=='Correct creator'
    assert result['description']==result['caption']=='Silicon sealant #waterproof'
    assert result['title_source']=='Caption excerpt'
    assert result['title']=='Silicon sealant #waterproof'
    assert result['duration']=='15.5s'
    assert result['hashtags']==['#waterproof']
    assert result['source_item_id']=='123456'

def test_generic_facebook_title_is_not_success():
    with pytest.raises(Unavailable,match='no title or caption'):
        parse_html(page([{'owner':{'name':'Viewer'}}]),URL)

def test_unrelated_caption_does_not_make_target_successful():
    with pytest.raises(Unavailable):
        parse_html(page([{'attachments':[{'media':{'id':'999'}}],'message':{'text':'Unrelated'}}]),URL)

def test_deferred_caption_is_joined_by_exact_story_id():
    result=parse_html(page([
        {'id':'story-target','attachments':[{'media':{'id':'123456'}}]},
        {'id':'story-other','message':{'text':'Unrelated caption'}},
        {'id':'story-target','message':{'text':'Target caption'},'actors':[{'name':'Target author'}]}
    ]),URL)
    assert result['description']=='Target caption'
    assert result['author']=='Target author'

def test_captionless_target_keeps_verified_metadata_as_partial():
    result=parse_html(page([{'id':'123456','owner':{'name':'Video author'},'length_in_second':12,
        'creation_story':{'id':'story-target','message':None}}]),URL)
    assert result['retrieval_status']=='partial'
    assert result['author']=='Video author' and result['duration']=='12s'
    assert result['title']==result['description']==''
    assert 'no title or caption' in result['retrieval_note']

def test_unrelated_metadata_cannot_make_empty_target_partial():
    with pytest.raises(Unavailable):
        parse_html(page([{'id':'123456'},{'id':'999','owner':{'name':'Other author'},'length_in_second':12}]),URL)
