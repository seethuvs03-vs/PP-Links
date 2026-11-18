"""Prevent two local server processes from resetting each other's durable work."""
import os

class InstanceLock:
    def __init__(self,path):self.path=path;self.file=None
    def acquire(self):
        self.file=open(self.path,'a+b')
        self.file.seek(0);self.file.write(b'0');self.file.flush();self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();raise RuntimeError('This database is already open in another PP Links server. Stop that server before starting another or importing Excel.')
        return self
