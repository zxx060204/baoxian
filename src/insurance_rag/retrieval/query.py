"""Validated filters and server-side publication isolation."""
FILTERS={'company_id','product_version_id','document_version_id','document_type','product_id','document_id','clause_id','section_id','insurance_type','sales_status','age'}

def validate(query,scope='chunk',filters=None,top_k=10):
    if not isinstance(query,str) or not query.strip() or len(query)>4000:raise ValueError('invalid_query')
    if scope not in ('product','chunk'):raise ValueError('invalid_scope')
    if isinstance(top_k,bool) or not isinstance(top_k,int) or not 1<=top_k<=100:raise ValueError('invalid_top_k')
    filters=filters or {}
    if not isinstance(filters,dict) or set(filters)-FILTERS:raise ValueError('invalid_filter')
    if any(not isinstance(v,str) or not v.strip() for k,v in filters.items() if k!='age'):raise ValueError('invalid_filter_value')
    if 'age' in filters and (scope!='product' or isinstance(filters['age'],bool) or not isinstance(filters['age'],int) or not 0<=filters['age']<=150):raise ValueError('invalid_age_filter')
    return query.strip(),filters

def filter_clauses(filters,scope,*,development=False):
    aliases={'document_version_id':'document_version_ids','document_type':'document_types','document_id':'document_ids'} if scope=='product' else {}
    terms=[{'term':{aliases.get(k,k):v}} for k,v in filters.items() if k!='age']
    if 'age' in filters:terms.extend([{'range':{'age_min':{'lte':filters['age']}}},{'range':{'age_max':{'gte':filters['age']}}}])
    # Development is a process-level capability, never a request filter.
    if not development:terms.append({'term':{'publication_status':'eligible'}})
    return terms

def rrf(routes,k=60,top_k=10):
    from .fusion import fuse
    return fuse(routes,method='rrf',rrf_k=k,top_k=top_k)['hits']


def record_matches(record,filters,scope):
    aliases={"document_type":"document_types","document_version_id":"document_version_ids","document_id":"document_ids"} if scope=="product" else {}
    for key,val in filters.items():
        if key=="age":
            age=record.get("age_range")
            if not age or not age["min"]<=val<=age["max"]:return False
            continue
        actual=record.get(aliases.get(key,key))
        if (val not in actual if isinstance(actual,list) else val!=actual):return False
    return True
