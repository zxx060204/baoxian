"""Public disclosure APIs and row-linked product detail pages.

Queries reproduce the site's published requests. No challenge bypass or login.
"""
import json,re,urllib.parse
from .expanded_adapters import REGISTRY,decode,absolute,parse_links,category,candidate,finish
from .transport import FetchError

def load(client,url,**kwargs):
    body,meta=client.get(url,catalog=True,**kwargs)
    try:return json.loads(decode(body))
    except ValueError as e:raise FetchError('non_json','PUBLIC_DIRECTORY_EXPECTED_JSON') from e

def discover(company,client,limit=None):
    if company!='中国人寿':raise ValueError('UNSUPPORTED_COMPANY')
    items=[];pages=[];page=1;size=15
    while True:
        if company=='中国人寿':
            url='https://www.e-chinalife.com/jw/web/json/app/product_info_publish/plugin/com.chinalife.yunque.ProductInfoPublish/service?'+urllib.parse.urlencode(dict(pageSize=size,page=page,type=1))
            data=load(client,url);rows=data.get('list',[]);total=int(data.get('count',0))
            for row in rows:
                if row.get('issell')!='1':continue
                for field,kind in [('clause','条款'),('ratestable','费率表'),('cashvalue','现金价值表'),('productdescription','产品说明书')]:
                    if row.get(field) and row[field]!='/':items.append(candidate(company,row['productname'],kind,row[field],url,row,sale_status='在售',version_or_date=row.get('datemodified')))
        pages.append(dict(url=url,page=page,total=total,rows=len(rows)))
        if (limit and len({r['product_identity'] for r in items})>=limit) or not rows or page*size>=total:break
        page+=1
    result=finish(company,items,pages,limit)
    result['discovery_complete']=not bool(limit)
    return result
