import openpyxl
from pplinks.db import init_db
from pplinks.jobs import Worker,submit_upload
from pplinks.whatsapp import parse_export
from pplinks.purchase_file import PurchaseFile
from pplinks import ROOT

def test_recurring_file_preserves_notes_and_appends_only_new(tmp_path):
    db=tmp_path/'db.sqlite3';init_db(db)
    publisher=PurchaseFile(db,tmp_path/'Purchase-Team-Master.xlsx')
    worker=Worker(db,ROOT/'config/categories.json',lambda url:{'title':'Product','description':'Details','final_url':url},on_complete=publisher.sync)
    first=submit_upload(db,'one.txt',parse_export('https://youtu.be/first'))
    worker.process_job(first)
    book=openpyxl.load_workbook(publisher.path)
    book['Facebook Reels']['N1']='Purchase status';book['Facebook Reels']['N2']='Quote requested'
    book.create_sheet('Team notes')['A1']='Keep this'
    book.save(publisher.path)
    second=submit_upload(db,'two.txt',parse_export('https://youtu.be/first\nhttps://youtu.be/second'))
    worker.process_job(second)
    book=openpyxl.load_workbook(publisher.path)
    assert book['Facebook Reels'].max_row==3
    assert book['Facebook Reels']['N2'].value=='Quote requested'
    assert book['Facebook Reels']['B3'].value=='https://youtu.be/second'
    assert book['Team notes']['A1'].value=='Keep this'
    assert publisher.sync()
    assert openpyxl.load_workbook(publisher.path)['Facebook Reels'].max_row==3

def test_locked_file_keeps_previous_version(tmp_path,monkeypatch):
    from pathlib import Path
    db=tmp_path/'db.sqlite3';init_db(db)
    publisher=PurchaseFile(db,tmp_path/'purchase.xlsx')
    assert publisher.sync()
    previous=publisher.path.read_bytes()
    def locked(*args):raise PermissionError('File in use')
    monkeypatch.setattr(Path,'replace',locked)
    assert not publisher.sync()
    assert publisher.path.read_bytes()==previous
    assert 'Close' in publisher.error
