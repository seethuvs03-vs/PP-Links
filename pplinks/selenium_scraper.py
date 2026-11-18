"""Standard Chrome rendering; reuse the notebook's metadata parser.

Optional application-owned Facebook session; no personal profile or challenge solving.
Each link gets a fresh browser which is closed even when retrieval fails.
"""
import logging
import time
import json
from contextlib import nullcontext
from urllib.parse import urlsplit
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from .scraper import Scraper,Unavailable,public_url

log=logging.getLogger(__name__)

class SeleniumScraper(Scraper):
    def __init__(self,headless=True,render_wait=3,chrome_binary='',driver_path='',max_page_mb=32,**kwargs):
        super().__init__(**kwargs)
        self.headless=headless;self.render_wait=max(0,float(render_wait))
        self.chrome_binary=chrome_binary;self.driver_path=driver_path
        self.facebook_session=None
        self.max_page_bytes=int(max_page_mb)*1024*1024
        if not 1024*1024<=self.max_page_bytes<=64*1024*1024:raise ValueError('SELENIUM_MAX_PAGE_MB must be between 1 and 64')

    def check_page_size(self,source):
        size=len(source.encode('utf-8'))
        if size>self.max_page_bytes:
            raise Unavailable(f'Rendered page is {size/1024/1024:.1f} MB; configured limit is {self.max_page_bytes//1024//1024} MB')

    def make_driver(self,profile=None,headless=None):
        options=Options()
        options.page_load_strategy='eager'
        if (self.headless if headless is None else headless):options.add_argument('--headless=new')
        options.add_argument('--window-size=1366,900')
        if profile is not None:
            options.add_argument('--user-data-dir='+str(profile))
            options.set_capability('goog:loggingPrefs',{'performance':'ALL'})
            options.add_experimental_option('prefs',{'credentials_enable_service':False,'profile.password_manager_enabled':False})
        # Keep Chrome's sandbox and normal automation identification enabled.
        if self.chrome_binary:options.binary_location=self.chrome_binary
        service=Service(executable_path=self.driver_path) if self.driver_path else Service()
        return webdriver.Chrome(service=service,options=options)

    def fetch_page(self,url):
        is_facebook=urlsplit(url).hostname in ('www.facebook.com','facebook.com','m.facebook.com','fb.watch','fb.me')
        context=self.facebook_session.use_profile() if is_facebook and self.facebook_session else nullcontext(None)
        with context as profile:
            return self._fetch_page(url,profile)

    def _fetch_page(self,url,profile=None):
        # Resolve only permitted redirects and reject HTTP access errors BEFORE
        # opening Chrome. Selenium is a rendering backend, not a restriction fallback.
        if profile is None:
            _,target=self.client.get(url)
        else:
            # Use the user's ordinary authenticated browser request, not an
            # anonymous HTTP preflight which cannot carry their Chrome session.
            public_url(url);self.client.allowed(url);target=url
        public_url(target)
        for attempt in range(self.client.retries+1):
            driver=None
            try:
                driver=self.make_driver(profile=profile) if profile is not None else self.make_driver()
                driver.set_page_load_timeout(self.client.timeout)
                driver.set_script_timeout(self.client.timeout)
                time.sleep(max(0,self.client.interval-(time.monotonic()-self.client.last)))
                self.client.last=time.monotonic()
                driver.get(target)
                WebDriverWait(driver,self.client.timeout).until(
                    lambda d:d.execute_script('return document.readyState') in ('interactive','complete'))
                time.sleep(min(self.render_wait,self.client.timeout))
                final=driver.current_url
                public_url(final)
                if profile is not None:self.check_browser_response(driver)
                if any(p in urlsplit(final).path.lower() for p in ('/login','/checkpoint','/challenge','/consent')):
                    raise Unavailable('Login or challenge page; Selenium retrieval stopped')
                self.client.allowed(final)
                if profile is not None:
                    from .facebook_session import logged_in
                    if not logged_in(driver):raise Unavailable('Facebook session expired or verification is required. Reconnect Facebook in the app.')
                source=driver.page_source
                self.check_page_size(source)
                if profile is not None:
                    from bs4 import BeautifulSoup
                    from .facebook_details import extract
                    # Facebook streams the target story after the document is ready.
                    deadline=time.monotonic()+self.client.timeout
                    while not extract(BeautifulSoup(source,'html.parser'),final).get('caption') and time.monotonic()<deadline:
                        time.sleep(1)
                        source=driver.page_source
                        self.check_page_size(source)
                        # Share links can redirect after document.readyState.
                        # Match the streamed story against the current post URL.
                        updated=driver.current_url
                        if updated!=final:
                            public_url(updated)
                            if any(p in urlsplit(updated).path.lower() for p in ('/login','/checkpoint','/challenge','/consent')):
                                raise Unavailable('Login or challenge page; Selenium retrieval stopped')
                            self.client.allowed(updated)
                            final=updated
                    if not logged_in(driver):
                        raise Unavailable('Facebook session expired or verification is required. Reconnect Facebook in the app.')
                    details=extract(BeautifulSoup(source,'html.parser'),final)
                    if not any(details.get(k) for k in ('caption','title','author','duration','posted_date','thumbnail')) and attempt<self.client.retries:
                        log.warning('Facebook post details did not finish loading; retrying (%s)',attempt+1)
                        time.sleep(2**attempt)
                        continue
                return source,final
            except (TimeoutException,WebDriverException) as exc:
                if attempt==self.client.retries:
                    raise Unavailable('Selenium/Chrome could not load the page. Check Chrome installation and network access. '+str(exc).splitlines()[0][:350]) from exc
                log.warning('Chrome attempt %s failed: %s',attempt+1,type(exc).__name__)
                time.sleep(2**attempt)
            finally:
                if driver is not None:
                    try:driver.quit()
                    except WebDriverException:log.warning('Chrome driver cleanup reported an error')
        raise Unavailable('Selenium retrieval failed')

    @staticmethod
    def check_browser_response(driver):
        frame=driver.execute_cdp_cmd('Page.getFrameTree',{})['frameTree']['frame']['id']
        statuses=[]
        # Inspect in memory only. Performance logs may contain session data and
        # must never be written to app logs, SQLite, or returned to the browser.
        for entry in driver.get_log('performance'):
            try:
                event=json.loads(entry['message'])['message'];params=event.get('params',{})
                if event.get('method')=='Network.responseReceived' and params.get('type')=='Document' and params.get('frameId')==frame:
                    statuses.append(int(params['response']['status']))
            except (ValueError,TypeError,KeyError):continue
        if not statuses:raise Unavailable('Could not verify the authenticated page response; retrieval stopped.')
        if any(s in (401,403,429) or s>=400 for s in statuses):
            raise Unavailable('Facebook denied the browser request (HTTP '+str(next(s for s in statuses if s>=400))+'). Retrieval stopped.')

    def __call__(self,url):
        try:
            result=super().__call__(url)
        except Unavailable as exc:
            if urlsplit(url).hostname in ('www.facebook.com','facebook.com','m.facebook.com','fb.watch','fb.me') and any(
                    marker in str(exc).lower() for marker in ('login','captcha','challenge','session expired')):
                connected=self.facebook_session and self.facebook_session.snapshot()['enabled']
                if not connected:
                    raise Unavailable('Facebook sign-in is not connected. Open Connections, click Connect Facebook, sign in in Chrome, then click Use this session. Retry this record after connecting.') from exc
                raise Unavailable('Facebook requested sign-in or verification. Open Connections and reconnect Facebook; complete any verification yourself in Chrome, then click Use this session and retry this record.') from exc
            raise
        result['retrieval_method']='Selenium / Chrome'
        return result
