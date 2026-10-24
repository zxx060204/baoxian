import sys,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import uvicorn
from insurance_rag.api.product_backend import create_app
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8782);a=p.parse_args();uvicorn.run(create_app(),host='127.0.0.1',port=a.port,log_level='warning')
