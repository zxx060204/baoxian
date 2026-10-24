"""Offline BM25 diagnostic baseline, not an OpenSearch/HNSW substitute."""
import math,re
from collections import Counter
from .query import validate,record_matches

def tokenize(text):
    # Preserve Latin/numeric terms and emit CJK bigrams plus single characters.
    out=[]
    for part in re.findall(r'[\u3400-\u9fff]+|[a-zA-Z0-9]+(?:\.[0-9]+)?',text.lower()):
        if re.fullmatch(r'[\u3400-\u9fff]+',part):out+=list(part)+[part[i:i+2] for i in range(len(part)-1)]
        else:out.append(part)
    return out

class OfflineBM25:
    def __init__(self,records,*,development=False,k1=1.2,b=.75):
        self.records=records;self.development=development;self.k1=k1;self.b=b
        if len({r['record_id'] for r in records})!=len(records):raise ValueError('duplicate_record_id')
        self.terms=[Counter(tokenize(r.get('content',r['raw_text']))) for r in records]
    def search(self,query,scope='chunk',filters=None,top_k=10):
        query,filters=validate(query,scope,filters,top_k)
        selected=[]
        for r,terms in zip(self.records,self.terms):
            if r['scope']!=scope or (not self.development and r['publication_status']!='eligible'):continue
            if record_matches(r,filters,scope):selected.append((r,terms))
        if not selected:return []
        n=len(selected);avg=sum(sum(t.values()) for _,t in selected)/n or 1
        wanted=set(tokenize(query));df={w:sum(w in t for _,t in selected) for w in wanted};hits=[]
        for r,t in selected:
            length=sum(t.values());score=0
            for w in wanted:
                f=t[w]
                if f:score+=math.log(1+(n-df[w]+.5)/(df[w]+.5))*f*(self.k1+1)/(f+self.k1*(1-self.b+self.b*length/avg))
            if score>0:hits.append({'record':r,'score':score,'ranks':{}})
        return sorted(hits,key=lambda h:(-h['score'],h['record']['record_id']))[:top_k]
