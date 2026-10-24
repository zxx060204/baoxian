"""Local-only model tokenization; byte budget is explicitly development-only."""
from pathlib import Path
from insurance_rag.parsing.common import file_hash,stable_hash

class ByteCounter:
    name='utf8_byte_upper_bound_v1'
    measured_model_tokens=False
    def __call__(self,text):return len(text.encode('utf8'))

class LocalTokenizer:
    measured_model_tokens=True
    def __init__(self,folder):
        folder=Path(folder).resolve(strict=True)
        if not folder.is_dir():raise ValueError('tokenizer_directory_required')
        assets=sorted(p for p in folder.rglob('*') if p.is_file() and
            (p.suffix in ('.json','.txt','.model') or p.name=='merges.txt'))
        if not assets:raise ValueError('tokenizer_assets_missing')
        from transformers import AutoTokenizer
        self.tokenizer=AutoTokenizer.from_pretrained(str(folder),local_files_only=True,trust_remote_code=False)
        self.name='local-tokenizer:'+stable_hash([(p.relative_to(folder).as_posix(),file_hash(p)) for p in assets])
    def __call__(self,text):
        return len(self.tokenizer.encode(text,add_special_tokens=True,truncation=False))
