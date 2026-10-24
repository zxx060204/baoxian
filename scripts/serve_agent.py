"""Serve a selected Agent runtime; local administrative access is explicit."""
import argparse,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
def main():
 from insurance_rag.runtime_env import load_env
 from insurance_rag.api.app import create_app
 import uvicorn
 p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8000);p.add_argument('--local-test',action='store_true');p.add_argument('--local-admin',action='store_true',help='Enable a separate same-origin loopback admin session');p.add_argument('--runtime',choices=('loop',),default='loop');a=p.parse_args()
 if a.local_admin and not a.local_test:p.error('--local-admin requires --local-test')
 load_env(Path(__file__).resolve().parents[1]/'.env')
 from insurance_rag.agent.loop_bootstrap import open_loop_application
 uvicorn.run(create_app(factory=open_loop_application,local_test_session=a.local_test,local_admin_session=a.local_admin),host='127.0.0.1',port=a.port,log_level='warning')
if __name__=='__main__':main()
