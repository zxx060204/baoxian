"""Shared PDF/Excel/archive checks; independent of prior acquisition outputs."""
import datetime,hashlib,re,unicodedata,zipfile,io,pathlib
from pypdf import PdfReader
import pdfplumber
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(body):return hashlib.sha256(body).hexdigest()


def norm(s):return re.sub(r'\s+','',unicodedata.normalize('NFKC',s or ''))


def classify(body):
    if body.startswith(b'%PDF-'):return 'pdf'
    if body.startswith(b'PK'):
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            return 'xlsx' if 'xl/workbook.xml' in z.namelist() else 'zip'
    if body.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):return 'xls'
    return 'unsupported'


def inspect_pdf(body):
    if not body.startswith(b'%PDF-') or b'%%EOF' not in body[-1024:]:raise ValueError('PDF_MAGIC_OR_EOF_FAILED')
    reader=PdfReader(io.BytesIO(body),strict=True);encrypted=reader.is_encrypted;permissions=None
    if encrypted:
        if not reader.decrypt(''):raise ValueError('NONEMPTY_PASSWORD_REQUIRED')
        permissions=int(reader.user_access_permissions)
    if not len(reader.pages):raise ValueError('ZERO_PAGES')
    texts=[]
    page_count=len(reader.pages)
    large=page_count>200 or len(body)>10*1024*1024
    indexes=[0,page_count-1] if large and page_count>1 else list(range(page_count))
    for index in indexes:
        page=reader.pages[index]
        stream=page.get_contents()
        if stream is not None:stream.get_data()
        texts.append(page.extract_text() or '')
    if not large:
        with pdfplumber.open(io.BytesIO(body),password='') as pdf:
            if len(pdf.pages)!=page_count:raise ValueError('PAGE_COUNT_MISMATCH')
            pdf.pages[0].extract_text();pdf.pages[-1].extract_text()
    return {'structural_status':'partial_large_pdf' if large else 'pass','pages':page_count,'encrypted':encrypted,'permissions':permissions,'full_text_parser':'pypdf','text_scope':'first_and_last_pages_only' if large else 'all_pages','inspected_page_numbers':[i+1 for i in indexes] if large else 'all','independent_parser_check':'not_performed_large_document' if large else 'pdfplumber page count and first/last text','note':'Original complete bytes retained; full table extraction and all-page checking deferred for large documents' if large else ''},texts


def safe_archive(body):
    z=zipfile.ZipFile(io.BytesIO(body));members=z.infolist()
    if len(members)>40 or sum(m.file_size for m in members)>512*1024*1024:raise ValueError('ZIP_RESOURCE_LIMIT')
    accepted=[];inventory=[]
    for m in members:
        name=m.filename.replace('\\','/');parts=pathlib.PurePosixPath(name).parts
        if '..' in parts or name.startswith('/') or ':' in name or (m.external_attr>>16)&0o170000==0o120000:raise ValueError('ZIP_UNSAFE_PATH')
        if m.is_dir():continue
        if m.file_size>512*1024*1024 or (m.compress_size and m.file_size/m.compress_size>500):raise ValueError('ZIP_MEMBER_LIMIT')
        suffix=pathlib.PurePosixPath(name).suffix.lower();inventory.append({'name':name,'bytes':m.file_size})
        if suffix in ['.pdf','.xlsx','.xls']:
            content=z.read(m);kind=classify(content)
            if kind not in ['pdf','xlsx','xls']:raise ValueError('ZIP_FILE_TYPE_MISMATCH')
            accepted.append((name,content,kind))
    if not accepted:raise ValueError('ZIP_NO_SUPPORTED_MATERIALS')
    return inventory,accepted

