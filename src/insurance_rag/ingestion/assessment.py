"""Read the current source rules and their independently projected capabilities."""
from .store import bundle
from .source_rule_reading import rule_packet
from .readiness import current_capabilities

def read_rules(app,owner,bundle_id,*,include_evidence=False):
 content=bundle(app,owner,bundle_id)['content']
 if content.get('scope')!='current_source_business':raise ValueError('retired_bundle_requires_migration')
 packet=rule_packet(app,content,include_evidence=include_evidence)
 return {**packet,'metrics':[],'coverage':{'status':packet['state']},'capabilities':current_capabilities(app,content)}
