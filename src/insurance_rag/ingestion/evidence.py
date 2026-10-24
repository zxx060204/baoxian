"""Validate literal source citations and retain original page character offsets."""
import re
from insurance_rag.onboarding.rule_text import clean_text

def literal(citation,sources):
 source=sources.get(citation['source_id'])
 if source is None:raise ValueError('rule_source_out_of_scope')
 text=source['text'];wanted=re.sub(r'\s','',citation['quote']);indexes=[i for i,v in enumerate(text) if not v.isspace()];compact=''.join(text[i] for i in indexes);start=compact.find(wanted)
 if not wanted or start<0:raise ValueError('rule_quote_not_literal')
 a=indexes[start];b=indexes[start+len(wanted)-1]+1
 result={'source_id':citation['source_id'],'quote':clean_text(text[a:b]),'raw_quote':text[a:b],'character_range':[a,b],'source':source['source'],'page_start':source.get('page_start',source['source']['page_number']),'page_end':source.get('page_end',source['source']['page_number']),'clause_id':source.get('clause_id'),'source_spans':source.get('source_spans',[])}
 if 'selection_origin' in source:result['selection_origin']=source['selection_origin']
 if 'page_character_range' in source:
  offset=source['page_character_range'][0];result['page_character_range']=[offset+a,offset+b]
 return result

