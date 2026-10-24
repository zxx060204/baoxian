"""Export offline bounded, evidence-preserving chunks without embedding or publication."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from insurance_rag.parsing.chunking import build_chunks
from insurance_rag.parsing.common import file_hash,read_json,write_json,write_jsonl,workspace_path


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--layout',required=True);ap.add_argument('--output',required=True)
    ap.add_argument('--budget',type=int,default=500)
    args=ap.parse_args();root=Path(__file__).resolve().parents[1]
    source=workspace_path(args.layout,root,must_exist=True);layout=read_json(source)
    output=workspace_path(args.output,root);output.mkdir(parents=True,exist_ok=False)
    result=build_chunks(layout,file_hash(source),args.budget)
    write_jsonl(output/'chunks.jsonl',result.pop('chunks'))
    write_json(output/'report.json',{**result,'layout':args.layout,'layout_sha256':file_hash(source),
        'chunks_file':'chunks.jsonl','note':'Offline UTF-8 byte budget is a conservative bound, not measured model tokens.'})


if __name__=='__main__':main()
