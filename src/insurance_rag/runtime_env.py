"""Literal .env loader. Does not expand variables, execute expressions or print values."""
import os,re
from pathlib import Path

def load_env(path,*,override=False):
    entries={}
    for line_number,line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(),1):
        text=line.strip()
        if not text or text.startswith('#'):continue
        match=re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)',text)
        if not match:raise ValueError('invalid_env_assignment_line_'+str(line_number))
        key,value=match.groups();value=value.strip()
        if len(value)>=2 and value[0]==value[-1] and value[0] in ('"',"'"):value=value[1:-1]
        if not value:continue
        if key in entries:raise ValueError('duplicate_env_key_line_'+str(line_number))
        entries[key]=value
    for key,value in entries.items():
        if override or key not in os.environ:os.environ[key]=value
    return len(entries)
