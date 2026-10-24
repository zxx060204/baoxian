"""Derived display text; original quotes and source ranges remain immutable."""
import re

_HEADING=re.compile(r'^(?:第[一二三四五六七八九十百0-9]+[条章节]|[一二三四五六七八九十]+[、．.]|[（(][一二三四五六七八九十0-9]+[）)]|[0-9]+[、．.]\s*)')
_CJK=re.compile(r'[\u3400-\u9fff]')

def display_quote(text):
    """Join physical prose lines, retaining paragraph and list boundaries."""
    lines=text.replace('\r\n','\n').replace('\r','\n').split('\n')
    out=''
    previous=''
    for line in lines:
        current=line.strip()
        if not out:out=current
        elif not current or not previous or _HEADING.match(current) or _HEADING.match(previous) or previous[-1:] in '。！？；：!?;:':out+='\n'+current
        else:
            # CJK continuations need no inserted space; Latin words do.
            separator='' if _CJK.search(previous[-1:]) or _CJK.search(current[:1]) or current[:1] in '，。！？；：、）】》”’' else ' '
            out+=separator+current
        previous=current
    return out

def context_notice(packet):
    recovery=packet.get('data',{}).get('context_recovery',{})
    notices=[]
    fallbacks=recovery.get('fallbacks',[])
    if fallbacks:
        for item in fallbacks:
            notices.append('“'+(item.get('clause_title') or '命中条款')+'”超过上下文预算，已采用命中分块及前'+str(item['neighbors_before'])+'块、后'+str(item['neighbors_after'])+'块的局部内容，未包含完整条款。')
        notices.append('局部内容可能缺少定义、免责或其他适用条件，请结合原件核对。')
    if recovery.get('omitted'):notices.append('另有条款未能纳入上下文，请查看原件；当前证据不足以保证完整适用条件。')
    return '\n'.join(dict.fromkeys(notices))


def stock_unit_issue(data):
    """Detect an annual-flow unit attached to a verified stock amount."""
    tables=([data['benefit_table']] if isinstance(data.get('benefit_table'),dict) else [])+data.get('benefit_tables',[])
    names=set()
    for table in tables:
        for column in table.get('columns',[]):
            if column.get('unit')=='CNY' and column.get('semantic_key') in ('current_source:base_contract:base_amount','current_source:base_contract:cash_value'):
                names.add(''.join(column['name'].split()))
                if column['semantic_key'].endswith(':base_amount'):names.add('基本保额')
    if not names:return False
    text=data.get('text','')
    pattern='(?:'+'|'.join(re.escape(n) for n in names)+r')([^。；;\n]{0,120}?)元\s*[/／]\s*年'
    for match in re.finditer(pattern,text):
        span=match.group(1)
        if re.search(r'不是|并非|不能|不要|不应|不代表|不等于',span):continue
        if re.search(r'年交保费|年缴保费|年度保费|当年度保险费|年交保险费|年金|养老金',span):continue
        return True
    return False


def display_result(data):
    """Derived public view; preserve quantitative fields and archived raw messages."""
    out=dict(data)
    if stock_unit_issue(out):
        out['text']='年度表已按本次条件生成。保额和现金价值的单位为元；年度行表示对应年度的数值，不代表每年领取。模型说明存在单位冲突，已省略，请查看结构化表格及其中的缺口。'
        out.pop('explanation',None)
        out['warnings']=list(dict.fromkeys(out.get('warnings',[])+['model_stock_unit_explanation_omitted']))
        out['quality_status']='verified_table_with_unsupported_explanation_omitted'
    out['display_text']=display_quote(out.get('text',''))
    return out
