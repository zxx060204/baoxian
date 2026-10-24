"""Integrity-checked native page checkpoints scoped to one parse configuration."""
from pathlib import Path
from .common import stable_hash,read_json,write_json


class PageCache:
    def __init__(self,directory,binding):
        self.directory=Path(directory);self.binding=binding
        self.directory.mkdir(parents=True,exist_ok=True)
        self.hits=0;self.writes=0

    def load(self,number):
        try:
            data=read_json(self.directory/f'page_{number:06d}.json')
            if data['binding']!=self.binding or data['checksum']!=stable_hash(data['payload']):return None
            payload=data['payload']
            if payload['page']['page_number']!=number or payload['page']['status']=='failed':return None
            self.hits+=1
            return payload
        except (OSError,ValueError,KeyError,TypeError):return None

    def save(self,number,page,blocks,tables):
        if page['status']=='failed':return
        payload={'page':page,'blocks':blocks,'tables':tables}
        write_json(self.directory/f'page_{number:06d}.json',
                   {'binding':self.binding,'checksum':stable_hash(payload),'payload':payload})
        self.writes+=1
