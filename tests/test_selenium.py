import pytest
from selenium.common.exceptions import WebDriverException
from pplinks.scraper import Unavailable
from pplinks.selenium_scraper import SeleniumScraper

URL='https://www.youtube.com/watch?v=test'

class Driver:
    current_url=URL
    page_source='<title>Rendered item</title><meta property="og:description" content="Created by JavaScript">'
    def __init__(self):self.closed=False;self.visits=[]
    def get(self,url):self.visits.append(url)
    def set_page_load_timeout(self,value):self.timeout=value
    def set_script_timeout(self,value):pass
    def execute_script(self,script):return 'complete'
    def quit(self):self.closed=True

@pytest.fixture
def scraper(monkeypatch):
    s=SeleniumScraper(interval=0,retries=0,render_wait=0)
    monkeypatch.setattr(s.client,'get',lambda url:('<title>Unrendered</title>',url))
    monkeypatch.setattr(s.client,'allowed',lambda url:None)
    monkeypatch.setattr('pplinks.selenium_scraper.public_url',lambda url:None)
    return s

def test_rendered_html_reuses_parser_and_closes_browser(scraper,monkeypatch):
    d=Driver();monkeypatch.setattr(scraper,'make_driver',lambda:d)
    result=scraper(URL)
    assert result['title']=='Rendered item'
    assert result['description']=='Created by JavaScript'
    assert result['retrieval_method'].startswith('Selenium')
    assert d.closed and d.visits==[URL]

def test_large_rendered_page_supported(scraper,monkeypatch):
    d=Driver();d.page_source='<title>Product details</title><!--'+('x'*(9*1024*1024))+'-->'
    monkeypatch.setattr(scraper,'make_driver',lambda:d)
    assert scraper(URL)['title']=='Product details'
    assert d.closed

def test_configured_size_limit_still_enforced(scraper,monkeypatch):
    scraper.max_page_bytes=1024*1024
    d=Driver();d.page_source='x'*(1024*1024+1)
    monkeypatch.setattr(scraper,'make_driver',lambda:d)
    with pytest.raises(Unavailable,match='configured limit'):scraper(URL)
    assert d.closed

@pytest.mark.parametrize('limit',[0,65])
def test_invalid_size_limit_rejected(limit):
    with pytest.raises(ValueError):SeleniumScraper(max_page_mb=limit)

def test_policy_denial_never_starts_browser(scraper,monkeypatch):
    def deny(url):raise Unavailable('Retrieval disallowed by platform robots policy')
    monkeypatch.setattr(scraper.client,'get',deny)
    monkeypatch.setattr(scraper,'make_driver',lambda:pytest.fail('Chrome must not start for a denied URL'))
    with pytest.raises(Unavailable,match='robots'):scraper(URL)

def test_login_redirect_stops_and_closes(scraper,monkeypatch):
    d=Driver();d.current_url='https://www.youtube.com/login'
    monkeypatch.setattr(scraper,'make_driver',lambda:d)
    with pytest.raises(Unavailable,match='Login'):scraper(URL)
    assert d.closed

def test_chrome_error_reports_actionable_message(scraper,monkeypatch):
    def broken():raise WebDriverException('driver unavailable')
    monkeypatch.setattr(scraper,'make_driver',broken)
    with pytest.raises(Unavailable,match='Chrome installation'):scraper(URL)

def test_challenge_content_does_not_become_metadata(scraper,monkeypatch):
    d=Driver();d.page_source='<title>Security check</title><input type="password">'
    monkeypatch.setattr(scraper,'make_driver',lambda:d)
    with pytest.raises(Unavailable,match='challenge'):scraper(URL)
    assert d.closed

@pytest.mark.parametrize('enabled',[False,True])
def test_facebook_challenge_explains_reconnection(scraper,monkeypatch,enabled):
    from types import SimpleNamespace
    scraper.facebook_session=SimpleNamespace(snapshot=lambda:{'enabled':enabled})
    monkeypatch.setattr(scraper,'fetch_page',lambda url:('<title>Log in</title>',url))
    with pytest.raises(Unavailable,match='Use this session') as error:
        scraper('https://www.facebook.com/reel/123')
    assert ('not connected' in str(error.value)) is (not enabled)

def test_standard_chrome_options_no_evasion(scraper,monkeypatch):
    captured={}
    def factory(**kwargs):captured.update(kwargs);return Driver()
    monkeypatch.setattr('pplinks.selenium_scraper.webdriver.Chrome',factory)
    scraper.make_driver()
    options=captured['options']
    assert '--headless=new' in options.arguments
    assert '--no-sandbox' not in options.arguments
    assert not options.experimental_options.get('excludeSwitches')
    assert not any('user-data-dir' in v or 'AutomationControlled' in v for v in options.arguments)

def test_saved_facebook_session_uses_authenticated_request(scraper,monkeypatch,tmp_path):
    from pplinks.facebook_session import FacebookSession
    session=FacebookSession(tmp_path,lambda:None)
    session.marker.write_text('enabled')
    scraper.facebook_session=session
    d=Driver();d.current_url='https://www.facebook.com/reel/123'
    monkeypatch.setattr(scraper.client,'get',lambda url:pytest.fail('Anonymous preflight must not replace the saved session'))
    monkeypatch.setattr(scraper,'make_driver',lambda profile:d if profile==session.profile else pytest.fail('Wrong profile'))
    monkeypatch.setattr(scraper,'check_browser_response',lambda driver:None)
    monkeypatch.setattr('pplinks.facebook_session.logged_in',lambda driver:True)
    source,final=scraper.fetch_page(d.current_url)
    assert 'Rendered item' in source and final==d.current_url and d.closed

def test_delayed_facebook_redirect_matches_loaded_post(scraper,monkeypatch):
    import json
    d=Driver();d.current_url='https://www.facebook.com/share/r/example'
    d.page_source='<script type="application/json">'+json.dumps({'id':'123','creation_story':{'message':{'text':'Target product'}}})+'</script>'
    monkeypatch.setattr(scraper,'make_driver',lambda profile:d)
    monkeypatch.setattr(scraper,'check_browser_response',lambda driver:None)
    monkeypatch.setattr('pplinks.facebook_session.logged_in',lambda driver:True)
    def advance(seconds):
        if seconds==1:d.current_url='https://www.facebook.com/reel/123'
    monkeypatch.setattr('pplinks.selenium_scraper.time.sleep',advance)
    source,final=scraper._fetch_page(d.current_url,profile='test')
    assert final=='https://www.facebook.com/reel/123'
    assert d.closed

def test_incomplete_facebook_page_retried(scraper,monkeypatch):
    import json
    scraper.client.retries=1;scraper.client.timeout=0
    first=Driver();first.current_url='https://www.facebook.com/reel/123'
    second=Driver();second.current_url=first.current_url
    second.page_source='<script type="application/json">'+json.dumps({'id':'123','creation_story':{'message':{'text':'Loaded product'}}})+'</script>'
    drivers=iter([first,second])
    monkeypatch.setattr(scraper,'make_driver',lambda profile:next(drivers))
    monkeypatch.setattr(scraper,'check_browser_response',lambda driver:None)
    monkeypatch.setattr('pplinks.facebook_session.logged_in',lambda driver:True)
    monkeypatch.setattr('pplinks.selenium_scraper.time.sleep',lambda seconds:None)
    source,final=scraper._fetch_page(first.current_url,profile='test')
    assert 'Loaded product' in source and first.closed and second.closed

def test_delayed_facebook_challenge_stops_without_retry(scraper,monkeypatch):
    scraper.client.retries=2
    d=Driver();d.current_url='https://www.facebook.com/share/r/example'
    calls=[]
    def factory(profile):
        calls.append(profile)
        return d
    monkeypatch.setattr(scraper,'make_driver',factory)
    monkeypatch.setattr(scraper,'check_browser_response',lambda driver:None)
    monkeypatch.setattr('pplinks.facebook_session.logged_in',lambda driver:True)
    def advance(seconds):
        if seconds==1:d.current_url='https://www.facebook.com/checkpoint/'
    monkeypatch.setattr('pplinks.selenium_scraper.time.sleep',advance)
    with pytest.raises(Unavailable,match='challenge'):
        scraper._fetch_page(d.current_url,profile='test')
    assert len(calls)==1 and d.closed
