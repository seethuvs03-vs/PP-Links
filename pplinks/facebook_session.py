"""User-operated Facebook login in a dedicated Chrome profile.

Password entry happens on Facebook. No credentials/cookies are returned to Flask,
written to SQLite, included in exports, or logged by this module.
"""
from contextlib import contextmanager
from pathlib import Path
import queue
import shutil
import threading
import time
from urllib.parse import urlsplit
from selenium.webdriver.common.by import By
from .scraper import Unavailable

def logged_in(driver):
    p=urlsplit(driver.current_url)
    if p.hostname not in ('www.facebook.com','facebook.com','m.facebook.com'):return False
    if any(s in p.path.lower() for s in ('login','checkpoint','challenge','recover')):return False
    if any(el.is_displayed() for el in driver.find_elements(By.CSS_SELECTOR,'input[type="password"], .g-recaptcha, #captcha')):return False
    # Only presence is checked; cookie values are never copied or persisted here.
    return bool(driver.get_cookie('c_user') and driver.get_cookie('xs'))

class FacebookSession:
    def __init__(self,root,driver_factory,login_timeout=600):
        self.root=Path(root).resolve();self.profile=self.root/'chrome-profile'
        self.marker=self.root/'enabled';self.driver_factory=driver_factory
        self.timeout=login_timeout;self.lock=threading.Lock();self.state_lock=threading.Lock()
        self.commands=queue.Queue();self.thread=None;self.stopping=threading.Event()
        self.state='saved' if self.marker.exists() else 'disconnected';self.message=''

    def set_state(self,state,message=''):
        with self.state_lock:self.state=state;self.message=message

    def snapshot(self):
        with self.state_lock:
            return dict(state=self.state,message=self.message,enabled=self.marker.exists(),has_profile=self.profile.exists())

    def begin(self):
        if not self.lock.acquire(blocking=False):raise ValueError('Chrome is busy processing a link or a sign-in window is already open. Try again when it finishes.')
        try:
            self.root.mkdir(parents=True,exist_ok=True)
            # Keep an existing saved session if reconnection is interrupted.
            # The profile lock prevents scraping during sign-in; retrieval still
            # verifies authentication before reading each Facebook post.
            self.commands=queue.Queue()
            self.set_state('opening','Opening Chrome. Sign in directly on Facebook.')
            self.thread=threading.Thread(target=self._login,daemon=True,name='facebook-sign-in')
            self.thread.start()
        except Exception:
            self.lock.release();raise

    def _login(self):
        driver=None
        try:
            driver=self.driver_factory(profile=self.profile,headless=False)
            driver.set_page_load_timeout(30)
            driver.get('https://www.facebook.com/login/')
            self.set_state('signing_in','Sign in in Chrome, then click Use this session here. Complete any verification yourself in Facebook.')
            deadline=time.monotonic()+self.timeout
            while not self.stopping.is_set() and time.monotonic()<deadline:
                try:command=self.commands.get(timeout=1)
                except queue.Empty:continue
                if command=='cancel':
                    self.set_state('saved' if self.marker.exists() else 'disconnected','Sign-in cancelled. The previous saved session is retained, if present.');return
                if command=='finish':
                    if not logged_in(driver):
                        self.set_state('signing_in','Facebook sign-in is not complete. Finish signing in or verification in Chrome, then try again.');continue
                    # Close Chrome first so its profile/cookies are flushed to disk.
                    driver.quit();driver=None
                    self.marker.write_text('enabled',encoding='utf-8')
                    self.set_state('saved','Session saved. Test one failed link before retrying all. Access to individual posts is not guaranteed.');return
            self.set_state('saved' if self.marker.exists() else 'disconnected','Sign-in was interrupted. The previous saved session is retained, if present. Otherwise connect again and click Use this session.')
        except Exception:
            # Driver exceptions can contain page/debug context. Keep it out of logs/UI.
            self.set_state('saved' if self.marker.exists() else 'error','Chrome sign-in could not finish. The previous saved session is retained, if present. Try connecting again if Facebook requests sign-in.')
        finally:
            if driver is not None:
                try:driver.quit()
                except Exception:pass
            self.lock.release()

    def finish(self):
        if self.snapshot()['state']!='signing_in':raise ValueError('Wait for the Facebook sign-in window to open first.')
        self.commands.put('finish')

    def cancel(self):self.commands.put('cancel')

    @contextmanager
    def use_profile(self):
        while not self.lock.acquire(timeout=.5):
            if self.stopping.is_set():raise Unavailable('Application stopped while waiting for Facebook sign-in')
        try:
            if self.stopping.is_set():raise Unavailable('Application is stopping')
            yield self.profile if self.marker.exists() else None
        finally:self.lock.release()

    def clear(self):
        if not self.lock.acquire(blocking=False):raise ValueError('Close the sign-in window or wait for the current link to finish first.')
        try:
            self.marker.unlink(missing_ok=True)
            # Delete only the fixed application-owned profile, never a personal one.
            if self.profile.is_symlink() or self.profile.resolve().parent!=self.root:
                raise ValueError('Unexpected profile location; removal stopped.')
            if self.profile.exists():shutil.rmtree(self.profile)
            self.set_state('disconnected','Saved browser session cleared.')
        finally:self.lock.release()

    def stop(self):
        self.stopping.set();self.cancel()
        if self.thread and self.thread.is_alive():self.thread.join(timeout=5)
