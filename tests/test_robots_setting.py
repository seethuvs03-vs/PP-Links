import pytest
from pplinks.scraper import PublicClient,Unavailable

def test_disabled_robots_skips_only_advisory_fetch(monkeypatch):
    c=PublicClient(respect_robots=False)
    monkeypatch.setattr(c,'request',lambda u:pytest.fail('No robots request expected'))
    c.allowed('https://www.facebook.com/reel/123')

def test_disabled_robots_still_rejects_login_redirect(monkeypatch):
    c=PublicClient(respect_robots=False)
    monkeypatch.setattr('pplinks.scraper.public_url',lambda u:None)
    monkeypatch.setattr(c,'request',lambda u:('',u,200))
    with pytest.raises(Unavailable,match='Login'):c.get('https://www.facebook.com/login')

def test_robots_still_enabled_by_default():
    assert PublicClient().respect_robots is True
