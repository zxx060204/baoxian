"""Load local configuration and check Bailian with one synthetic text, never PDF content."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from insurance_rag.runtime_env import load_env
from insurance_rag.parsing.common import workspace_path,write_json
from insurance_rag.indexing.bailian import BailianEmbedding

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--env-file',default='.env');p.add_argument('--output')
    a=p.parse_args();root=Path(__file__).resolve().parents[1]
    report={'status':'failed','input_scope':'single_synthetic_text_no_pdf_content'}
    try:
        load_env(workspace_path(a.env_file,root,must_exist=True))
        provider=BailianEmbedding.from_environment()
        vector=provider.encode(['这是一条向量接口连通性测试文本。'])[0]
        report.update(status='passed',model=provider.model,dimension=len(vector),binding=provider.binding,usage=provider.stats)
    except (ValueError,RuntimeError,OSError) as error:
        report['error_type']=type(error).__name__
        report['reason']=str(error) if isinstance(error,(ValueError,RuntimeError)) else 'local_configuration_unavailable'
    if a.output:
        out=workspace_path(a.output,root)
        if out.exists():raise FileExistsError('probe_output_exists')
        write_json(out,report)
    print(json.dumps(report,ensure_ascii=False,indent=2));return 0 if report['status']=='passed' else 1
if __name__=='__main__':raise SystemExit(main())
