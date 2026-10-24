"""Source-only product-version review drafts; no guessed canonical identities."""
from collections import defaultdict
import re

FILING=re.compile(r'[\u4e00-\u9fff]{2,30}\s*[\[【（(]\s*\d{4}\s*[\]】）)]\s*[\u4e00-\u9fff]{2,20}\s*\d+\s*号')
CODE=re.compile(r'(?:条款编号|备案编号|注册编号|险种代码|产品代码)\s*[:：]\s*[A-Za-z0-9_-]{3,50}')


def identity_review_draft(entries, references):
    grouped=defaultdict(list);mappings=[]
    for entry in entries:
        findings=[]
        reference=references.get(entry['document_version_id'])
        if reference:
            if reference['file_sha256']!=entry['file_sha256']:
                raise ValueError('Identity evidence original hash mismatch')
            for page in reference['pages']:
                text=page['reference_text']
                for expression in (FILING,CODE):
                    for match in expression.finditer(text):
                        findings.append({'text':match[0],'page_number':page['page_number'],
                            'text_offset':[match.start(),match.end()],'source_file':entry['file_path'],
                            'file_sha256':entry['file_sha256'],'method':'original_pdfium_text',
                            'interpretation':'candidate_only_not_a_product_version'})
        grouped[(entry['company_id'],entry['source_product_key'])].append({'document_version_id':entry['document_version_id'],
            'type':entry['document_type'],'product_name':entry['product_name'],'source_file':entry['file_path'],
            'file_sha256':entry['file_sha256'],'catalog_evidence':entry.get('source_version_evidence',{}),'body_evidence':findings})
        mappings.append({'document_version_id':entry['document_version_id'],'source_product_key':entry['source_product_key'],
            'file_sha256':entry['file_sha256'],
            'status':'pending_review','product_id':None,'product_version_id':None,
            'reviewer':'','reviewed_at':'','evidence':[]})
    groups=[]
    for (company,key),docs in grouped.items():
        filings={re.sub(r'\s+','',f['text']) for d in docs if d['type']=='terms' for f in d['body_evidence']}
        names={d['product_name'] for d in docs}
        reasons=['canonical_product_version_requires_review']
        if len(filings)>1:reasons.append('multiple_terms_filing_candidates')
        if len(names)>1:reasons.append('source_names_differ')
        if not filings:reasons.append('terms_filing_evidence_missing_in_sample')
        groups.append({'company_id':company,'source_product_key':key,'documents':docs,
            'review_reasons':reasons,'status':'pending_review','source_group_is_not_canonical_product':True})
    return {'schema_version':1,'groups':groups,'mapping_template':{'schema_version':1,'entries':mappings},
        'publication_status':'quarantined','note':'Catalog grouping and filing candidates are evidence, never automatic verified product linkage.'}
