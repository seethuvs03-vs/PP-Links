"""Maintain one purchase workbook; preserve purchase columns and custom sheets."""
from copy import copy
from pathlib import Path
import threading
import uuid
import openpyxl
from .export import export_excel,HEADERS
from .db import connect,now
from .urls import normalize_url

class PurchaseFile:
    def __init__(self,db,path):
        self.db=db;self.path=Path(path);self.lock=threading.Lock()
        self.error='';self.updated_at=''

    def sync(self):
        with self.lock:
            temp=None
            try:
                before=self.path.stat().st_mtime_ns if self.path.exists() else None
                fresh=openpyxl.load_workbook(export_excel(self.db))
                if self.path.exists():
                    book=openpyxl.load_workbook(self.path)
                    sheet=book['Facebook Reels'];source=fresh['Facebook Reels']
                    if [sheet.cell(1,c).value for c in range(1,14)]!=HEADERS:
                        raise ValueError('Master workbook columns have changed; restore the original first 13 columns before updating.')
                    with connect(self.db) as c:
                        aliases={r['url']:r['item_id'] for r in c.execute('SELECT * FROM aliases')}
                    def identity(sheet,row):
                        for col in (2,3):
                            try:
                                key=normalize_url(str(sheet.cell(row,col).value or ''))
                                if key in aliases:return ('id',aliases[key])
                            except ValueError:pass
                        return ('url',sheet.cell(row,2).value)
                    existing={identity(sheet,r):r for r in range(2,sheet.max_row+1)}
                    for r in range(2,source.max_row+1):
                        key=identity(source,r)
                        dest=existing.get(key,sheet.max_row+1)
                        for col in range(1,14):
                            a=source.cell(r,col);b=sheet.cell(dest,col)
                            b.value=a.value;b._style=copy(a._style)
                            b.hyperlink=copy(a.hyperlink)
                        sheet.cell(dest,1).value=dest-1
                        existing[key]=dest
                    sheet.auto_filter.ref=f'A1:{sheet.cell(1,sheet.max_column).column_letter}{sheet.max_row}'
                    # Refresh app-owned support sheets; custom purchase sheets stay.
                    for name in fresh.sheetnames:
                        if name=='Facebook Reels':continue
                        if name in book:book.remove(book[name])
                        src=fresh[name];dst=book.create_sheet(name)
                        for row in src:
                            for a in row:
                                b=dst.cell(a.row,a.column,a.value);b._style=copy(a._style)
                                if a.hyperlink:b.hyperlink=copy(a.hyperlink)
                        for key,dim in src.column_dimensions.items():dst.column_dimensions[key]=copy(dim)
                        dst.freeze_panes=src.freeze_panes;dst.auto_filter.ref=src.auto_filter.ref
                else:book=fresh
                book['Summary']['B2']=self.path.name
                self.path.parent.mkdir(parents=True,exist_ok=True)
                temp=self.path.with_name('.'+self.path.stem+'.'+uuid.uuid4().hex+'.xlsx')
                book.save(temp)
                after=self.path.stat().st_mtime_ns if self.path.exists() else None
                if after!=before:raise ValueError('The workbook was edited during this update. Please retry.')
                temp.replace(self.path)
                self.error='';self.updated_at=now()
                return True
            except PermissionError:
                self.error='Close the purchase master in Excel, then click Update master file. The previous file and database are safe.'
            except Exception as exc:
                self.error='Purchase file was not updated: '+str(exc)[:300]
            finally:
                if temp:
                    try:temp.unlink(missing_ok=True)
                    except OSError:pass
            return False
