import copy, json, tempfile, unittest
from pathlib import Path
from insurance_rag.parsing.common import stable_hash, file_hash

class FormulaReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.audit={'status':'confirmed','reviewer':'independent-synthetic-fixture',
                    'reviewed_at':'2026-10-16T00:00:00+08:00','evidence':['synthetic evidence only']}
        self.text='示例：投保年龄40岁，基本保额10000元，费率100元，现金价值200元。按基本保额除以10000乘以费率与现金价值的较大值。'
        self.documents=[]
        for kind in ('brochure','terms'):
            pdf=self.root/(kind+'.pdf');pdf.write_bytes(('%PDF-synthetic-'+kind).encode())
            layout=self.root/(kind+'.json');layout.write_text(json.dumps({'pages':[{'page_number':1,'raw_text':self.text,'width':100,'height':100}]}),encoding='utf8')
            self.documents.append({'document_version_id':kind,'product_version_id':'v1','document_type':kind,
                'source_file':pdf.name,'source_sha256':file_hash(pdf),'layout_file':layout.name,'layout_sha256':file_hash(layout)})
        self.manifest={'documents':self.documents}
        self.products=[{'product_id':'p1','product_version_id':'v1','company_id':'synthetic'}]
        self.packets=[{'document_version_id':d['document_version_id'],'page_number':1} for d in self.documents]
        self.sid=stable_hash(['brochure',1,self.documents[0]['layout_sha256']])
        param=lambda name:{'type':'parameter','name':name}
        self.proposal={'name':'synthetic example formula','basis':'explicit_formula',
            'parameters':{'amount':{'unit':'CNY','min':'0'},
                'rate':{'unit':'CNY','source_kind':'exact_record','exact_lookup':{'tool':'query_rate','value_role':'premium','required_axes':['age'],'unit':'CNY','conditions':[]}},
                'cash':{'unit':'CNY','source_kind':'exact_record','exact_lookup':{'tool':'query_cash_value','value_role':'cash_value','required_axes':['age'],'unit':'CNY','conditions':[]}}},
            'expression_tree':{'type':'multiply','args':[{'type':'divide','args':[param('amount'),{'type':'constant','value':'10000'}]},
                {'type':'max','args':[param('rate'),param('cash')]}]},
            'conditions':[],'required_scenario':{},'result_unit':'CNY','rounding':{'mode':'exact','payment_rounding_specified':False},
            'evidence':[{'source_id':self.sid,'quote':self.text}],'limitations':['synthetic only']}
        self.calls=[];outer=self
        class Tools:
            failed=False
            def call(self,kind,args):
                outer.calls.append(kind)
                if self.failed:return {'status':'unavailable','reason_code':'missing_table'}
                return {'status':'ok','data':{'record_id':kind+'-1','product_version_id':'v1',
                    'value':'100' if kind=='query_rate' else '200','unit':args['unit'],
                    'axes':args['axes'],'conditions':args['conditions'],'source':{'page_number':1}}}
            def validate_numeric(self,data):
                if data['product_version_id']!='v1':raise ValueError('numeric_version_conflict')
        self.tools=Tools();self.tools.m={'snapshot_id':stable_hash(self.manifest)}
        self.cases=[{'case_id':'official-synthetic-40','product_version_id':'v1','parameters':{'amount':'10000','rate':'999'},
                     'conditions':{'age':40},'audit':self.audit,
                     'target':{'source_id':self.sid,'quote':self.text,'value':'200','unit':'CNY'}}]
    def tearDown(self):self.temp.cleanup()

class AdmissionTests(unittest.TestCase):
    def product(self):
        return {'product_version_id':'v1','product_name':'synthetic','document_types':['terms','brochure','premium_rate','cash_value'],
                'business_capabilities':{k:{'status':'available_development_source','eligible_records':10} for k in ('premium_rate','cash_value')}}


if __name__=='__main__':unittest.main()
