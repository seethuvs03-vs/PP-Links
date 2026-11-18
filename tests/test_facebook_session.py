import json
import time
import pytest
from pplinks.facebook_session import FacebookSession,logged_in
from pplinks.selenium_scraper import SeleniumScraper
from pplinks.scraper import Unavailable
from pplinks import create_app

class Driver:
    current_url='https://www.facebook.com/'
    authenticated=False
    closed=False
    def set_page_load_timeout(self,value):pass
    def get(self,url):pass
    def find_elements(self,*args):return []
    def get_cookie(self,name):return {'value':'secret-test-value'} if self.authenticated else None
    def quit(self):self.closed=True

def wait_for(predicate):
    deadline=time.monotonic()+3
    while not predicate():
        assert time.monotonic()<deadline
        time.sleep(.01)

def test_login_save_reuse_clear(tmp_path):
    d=Driver();s=FacebookSession(tmp_path/'session',lambda **kwargs:d)
    s.begin();wait_for(lambda:s.snapshot()['state']=='signing_in')
    assert not logged_in(d)
    with pytest.raises(ValueError):s.clear()
    d.authenticated=True;s.finish()
    wait_for(lambda:s.snapshot()['state']=='saved')
    s.thread.join(1)
    assert d.closed and 'secret-test-value' not in json.dumps(s.snapshot())
    with s.use_profile() as profile:assert profile==s.profile
    assert FacebookSession(s.root,lambda:None).snapshot()['state']=='saved'
    s.clear();assert not s.marker.exists()

def test_checkpoint_is_not_saved(tmp_path):
    d=Driver();d.authenticated=True;d.current_url='https://www.facebook.com/checkpoint/'
    s=FacebookSession(tmp_path,lambda **kwargs:d)
    s.begin();wait_for(lambda:s.snapshot()['state']=='signing_in')
    s.finish();wait_for(lambda:'not complete' in s.snapshot()['message'])
    assert not s.marker.exists()
    s.stop();assert d.closed

@pytest.mark.parametrize('ending',['cancel','timeout','startup_error'])
def test_interrupted_reconnection_keeps_saved_session(tmp_path,ending):
    d=Driver()
    def factory(**kwargs):
        if ending=='startup_error':raise RuntimeError('Chrome startup failed')
        return d
    s=FacebookSession(tmp_path,factory,login_timeout=0 if ending=='timeout' else 600)
    s.marker.write_text('enabled')
    s.begin()
    if ending=='cancel':
        wait_for(lambda:s.snapshot()['state']=='signing_in')
        s.cancel()
    s.thread.join(3)
    assert not s.thread.is_alive()
    assert s.snapshot()['enabled'] and s.snapshot()['state']=='saved'
    with s.use_profile() as profile:assert profile==s.profile

@pytest.mark.parametrize('status',[200,403,429])
def test_browser_response(status):
    class Response:
        def execute_cdp_cmd(self,*args):return {'frameTree':{'frame':{'id':'main'}}}
        def get_log(self,*args):return [{'message':json.dumps({'message':{'method':'Network.responseReceived','params':{'type':'Document','frameId':'main','response':{'status':status}}}})}]
    if status==200:SeleniumScraper.check_browser_response(Response())
    else:
        with pytest.raises(Unavailable,match=str(status)):SeleniumScraper.check_browser_response(Response())

def test_connection_routes_csrf_and_no_launch_on_get(tmp_path):
    app=create_app({'TESTING':True,'DATABASE':str(tmp_path/'db.sqlite3'),'FACEBOOK_SESSION_DIR':tmp_path/'session','SCRAPER_BACKEND':'selenium'},start_worker=False)
    client=app.test_client()
    assert client.get('/connections').status_code==200
    assert not (tmp_path/'session').exists()
    assert client.post('/connections/connect').status_code==400
    assert client.get('/api/facebook-session').json['state']=='disconnected'
