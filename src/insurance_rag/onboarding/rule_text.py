"""Persist readable rule text separately from immutable source/release evidence."""
import copy,re
from insurance_rag.agent.store import Conflict
from insurance_rag.parsing.common import stable_hash

POLICY='rule_linebreaks_v1'
TEXT_FIELDS={'text','quote','footnotes','description','semantic_note','rule_name'}

def clean_text(text):
 # Handle actual line endings and literal escaped line endings without decoding JSON.
 text=re.sub(r'\\r\\n|\\r|\\n', '\n', text)
 def join(match):
  left=text[match.start()-1:match.start()];right=text[match.end():match.end()+1]
  return ' ' if left and right and left.isascii() and right.isascii() and left.isalnum() and right.isalnum() else ''
 return re.sub(r'[\r\n]+',join,text).strip()

def normalized_content(value,key=None):
 if isinstance(value,dict):return {k:normalized_content(v,k) for k,v in value.items()}
 if isinstance(value,list):return [normalized_content(v,key) for v in value]
 if isinstance(value,str) and key in TEXT_FIELDS:return clean_text(value)
 return copy.deepcopy(value)

