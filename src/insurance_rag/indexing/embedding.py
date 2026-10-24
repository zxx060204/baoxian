"""Local embedding adapter and model-bound resumable SQLite cache."""
import json,math,sqlite3
from pathlib import Path
from insurance_rag.parsing.common import stable_hash,file_hash

def validate_vector(vector,dimension):
    if not isinstance(vector,(list,tuple)) or len(vector)!=dimension:raise ValueError('embedding_dimension_mismatch')
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in vector):
        raise ValueError('embedding_nonfinite_or_invalid')
    if not any(vector):raise ValueError('embedding_zero_vector')
    return list(vector)

class LocalEmbedding:
    def __init__(self,folder,dimension,*,max_tokens=512):
        folder=Path(folder).resolve(strict=True)
        assets=sorted(p for p in folder.rglob('*') if p.is_file())
        if not assets:raise ValueError('local_model_empty')
        from sentence_transformers import SentenceTransformer
        self.model=SentenceTransformer(str(folder),local_files_only=True,trust_remote_code=False,device='cpu')
        actual=self.model.get_sentence_embedding_dimension()
        if actual!=dimension:raise ValueError('embedding_dimension_mismatch')
        self.dimension=dimension
        self.max_tokens=min(max_tokens,self.model.max_seq_length)
        self.binding={'provider':'local_sentence_transformers','revision':stable_hash([
            (p.relative_to(folder).as_posix(),file_hash(p)) for p in assets]),'dimension':dimension,
            'normalize':True,'model_max_tokens':self.model.max_seq_length,'prompt_policy':'explicit_empty_v1'}
    def encode(self,texts):
        for text in texts:
            if len(self.model.tokenizer.encode(text,add_special_tokens=True,truncation=False))>self.max_tokens:
                raise ValueError('model_input_would_truncate')
        return self.model.encode(texts,prompt="",normalize_embeddings=True,show_progress_bar=False).tolist()

class EmbeddingCache:
    def __init__(self,path):
        self.db=sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS embedding(key TEXT PRIMARY KEY,binding TEXT NOT NULL,text_hash TEXT NOT NULL,vector TEXT NOT NULL)')
    def close(self):self.db.close()
    def encode(self,texts,provider,*,batch_size=16):
        if isinstance(batch_size,bool) or not isinstance(batch_size,int) or batch_size<1:raise ValueError('invalid_batch_size')
        binding=stable_hash(provider.binding);dimension=provider.binding['dimension']
        result=[None]*len(texts);missing={};hits=0
        for i,text in enumerate(texts):
            key=stable_hash([binding,text]);row=self.db.execute('SELECT binding,text_hash,vector FROM embedding WHERE key=?',(key,)).fetchone()
            if row:
                if row[:2]!=(binding,stable_hash(text)):raise ValueError('embedding_cache_binding_changed')
                result[i]=validate_vector(json.loads(row[2]),dimension);hits+=1
            else:missing.setdefault(key,{'text':text,'indices':[]})['indices'].append(i)
        items=list(missing.items());computed=0
        for start in range(0,len(items),batch_size):
            batch=items[start:start+batch_size];vectors=provider.encode([x['text'] for _,x in batch])
            if len(vectors)!=len(batch):raise ValueError('embedding_batch_size_mismatch')
            validated=[validate_vector(v,dimension) for v in vectors]
            with self.db:
                for (key,item),vector in zip(batch,validated):
                    self.db.execute('INSERT OR REPLACE INTO embedding VALUES(?,?,?,?)',
                        (key,binding,stable_hash(item['text']),json.dumps(vector,allow_nan=False)))
                    for i in item['indices']:result[i]=vector
            computed+=len(batch)
        return result,{'cache_hits':hits,'computed_unique_texts':computed,'binding_hash':binding}
