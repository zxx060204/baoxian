"""Model selects bounded source spans/numeric tokens; the program owns quotations."""
from copy import deepcopy
from decimal import Decimal
import re

from insurance_rag.illustration.schema import Proposals


class EvidenceSelectionError(ValueError):
    def __init__(self, code, **detail):
        super().__init__(code)
        self.detail = detail


def catalog(sources, *, focus_names=()):
    """Partition every supplied source without dropping characters; retain exact offsets."""
    from insurance_rag.illustration.preparation import source_number
    spans = {}; numbers = {}; public_spans = []; public_numbers = []; validation_sources=list(sources)
    for source in sources:
        text = source['text']
        offset=0
        pattern=r'(?<![\d.．零〇一二两三四五六七八九十百千万亿])(?:\d{1,3}(?:[,，]\d{3})+(?:[.．]\d+)?|\d+(?:[.．]\d+)?|[零〇一二两三四五六七八九十百千]+)[%％]?(?![零〇一二两三四五六七八九十百千万亿])'
        tokens=list(re.finditer(pattern,text))
        while offset<len(text):
            end=min(offset+400,len(text))
            newline=text.rfind('\n',offset,end)
            if newline>offset+200:end=newline+1
            # Never manufacture a numeric token by cutting it at a span boundary.
            for token in tokens:
                if token.start()<end<token.end():end=token.end();break
            piece=text[offset:end];a=offset;offset=end
            if not piece.strip():continue
            eid='e'+str(len(spans)+1)
            origin=source.get('source',{})
            span_source={**source,'source_id':source['source_id']+':p0:'+str(a)+'-'+str(end),'text':piece,
                         'selection_origin':{'source_id':source['source_id'],'character_range':[a,end],'chunk_ids':source.get('chunk_ids',[])}}
            if source.get('page_character_range'):
                origin_offset=source['page_character_range'][0]
                span_source['page_character_range']=[origin_offset+a,origin_offset+end]
            validation_sources.append(span_source)
            spans[eid]={'source_id':span_source['source_id'],'quote':piece,'character_range':[a,end],
                        'document_id':origin.get('document_version_id'),'page':source.get('page_start',origin.get('page_number'))}
            public_spans.append([eid,source['source_id'],piece])
            for match in tokens:
                if not a<=match.start()<match.end()<=end:continue
                # Numerical illustration rows remain readable, but cannot prove a formula constant.
                line_a=text.rfind('\n',0,match.start())+1;line_b=text.find('\n',match.end())
                line=text[line_a:line_b if line_b>=0 else len(text)]
                arabic=re.findall(r'\d{1,3}(?:[,，]\d{3})+(?:[.．]\d+)?|\d+(?:[.．]\d+)?',line)
                grid=not re.search(r'[\u4e00-\u9fff%％]',line) and len(arabic)>=5 and sum(Decimal(n.replace('．','.').replace(',','').replace('，',''))>1000 for n in arabic)>=2
                if grid:continue
                token=match.group();percent=token.endswith(('%','％'));raw=token[:-1] if percent else token
                try:value=source_number(raw.replace('．','.'))
                except ValueError:continue
                if percent:value/=100
                nid='n'+str(len(numbers)+1)
                numbers[nid]={'value':str(value),'raw_value':token,'conversion':'percent' if percent else 'identity',
                              'evidence_id':eid,'character_range':[match.start(),match.end()]}
                public_numbers.append([nid,eid,token,str(value)])
    # Put actual target definitions ahead of cover pages and illustration rows;
    # preserve every span and its original ID/offset for independent checking.
    if focus_names:
        def score(row):
            text=re.sub(r'\s','',row[2])
            return (any(name in text for name in focus_names) and bool(re.search('给付|乘以|之和|递增|较大|较小|等于|公式',text)),any(name in text for name in focus_names))
        public_spans.sort(key=score,reverse=True)
        order={row[0]:i for i,row in enumerate(public_spans)}
        public_numbers.sort(key=lambda row:order[row[1]])
    return {'spans': spans, 'numbers': numbers, 'validation_sources':validation_sources,
            'wire': {'evidence_columns': ['evidence_id', 'source_id', 'literal_text'], 'evidence': public_spans,
                     'number_columns': ['number_id', 'evidence_id', 'literal_number', 'normalized_value'], 'numbers': public_numbers}}


def selection_schema(target):
    schema = deepcopy(Proposals.model_json_schema())
    schema['$defs']['Citation'] = {'type': 'object', 'additionalProperties': False,
                                  'properties': {'evidence_id': {'type': 'string'}}, 'required': ['evidence_id']}
    formula = schema['$defs']['FormulaProposal']
    formula['properties']['metric_code']['enum'] = [target]
    formula['properties'].pop('constants')
    schema['$defs'].pop('ConstantProof', None)
    predicate=schema['$defs']['Predicate']['properties']
    predicate['number_id']={'type':'string'}
    predicate['value']={'type':'string','enum':['true','false']}
    # The old open dict schema left the most failure-prone part (AST) unspecified.
    def obj(properties, required=None):
        return {'type':'object','additionalProperties':False,'properties':properties,'required':required or list(properties)}
    text={'type':'string'};condition={'$ref':'#/$defs/SelectedCondition'};expression={'$ref':'#/$defs/SelectedExpression'}
    compare={'type':{'const':'compare'},'parameter':text,'operator':{'enum':['eq','ne','lt','le','gt','ge']}}
    schema['$defs']['SelectedCondition']={'anyOf':[
        obj({**compare,'number_id':text}),obj({**compare,'right_parameter':text}),
        obj({**compare,'value':{'enum':['true','false']}}),
        obj({'type':{'enum':['all','any']},'args':{'type':'array','items':condition,'minItems':1,'maxItems':20}}),
        obj({'type':{'const':'not'},'arg':condition})]}
    schema['$defs']['SelectedExpression']={'anyOf':[
        obj({'type':{'const':'parameter'},'name':text}),
        obj({'type':{'const':'constant'},'number_id':text,'unit':{'enum':['CNY','year','dimensionless','CNY/year']}}),
        obj({'type':{'enum':['add','sum','multiply','product','min','max']},'args':{'type':'array','items':expression,'minItems':1,'maxItems':128}}),
        obj({'type':{'enum':['subtract','divide','power']},'args':{'type':'array','items':expression,'minItems':2,'maxItems':2}}),
        obj({'type':{'enum':['negate','abs']},'arg':expression}),
        obj({'type':{'const':'year_count'},'arg':expression}),
        obj({'type':{'const':'unavailable'},'reason':text}),
        obj({'type':{'const':'piecewise'},'branches':{'type':'array','minItems':1,'maxItems':64,'items':obj({'condition':condition,'value':expression})},'otherwise':expression})]}
    formula['properties']['expression_tree']={'anyOf':[expression,{'type':'null'}]}
    formula['properties']['applicability']={'anyOf':[condition,{'type':'null'}]}
    schema['$defs']['VariableDefinition']['properties']['expression_tree']={'anyOf':[expression,{'type':'null'}]}
    schema['properties']['formulas']['maxItems']=1
    return schema


INSTRUCTION = '''证据选择协议（覆盖旧的quote/constants写法）：
所有evidence对象只含evidence_id字段，值必须选择给定列表中实际支持该关系的ID，不能固定选择列表首项或产品封面。不得生成source_id、quote或字符偏移。引用全文由程序恢复。
不要输出constants字段。表达式常量使用type=constant、number_id和unit；number_id选对应原文数字的实际ID，数值由程序读取，不输出value。
数字比较条件使用type=compare、parameter、operator和实际number_id；布尔条件value仍可为true/false字符串，变量比较用right_parameter。
完整的表达式变量也遵守相同协议。必须从number列表选择；列表的normalized_value已完成百分数转换，不要再次转换。
没有可证明的数字时返回complete=false并说明missing；不能从利益演示数值推导公式、不能拿不相关数字证明公式。选中的片段必须覆盖公式和必要定义/比例表；多个片段可分别引用，不拼接引文。
同一evidence_id可多次使用。仅选择证据和数值不等于公式已通过业务验证。'''


def bind(raw, selected):
    """Translate the selection protocol into existing strict validator input."""
    if not isinstance(raw, dict) or set(raw) != {'formulas'} or not isinstance(raw['formulas'], list) or len(raw['formulas']) > 1:
        raise EvidenceSelectionError('invalid_selection_envelope', field='formulas')
    result = deepcopy(raw)
    for fi, formula in enumerate(result['formulas']):
        if not isinstance(formula, dict):
            raise EvidenceSelectionError('invalid_selected_formula', field=f'formulas[{fi}]')
        if 'constants' in formula:
            raise EvidenceSelectionError('model_constants_not_allowed', field=f'formulas[{fi}].constants')
        proofs = {}; used_spans = set();node_count=0

        def citation(value, path):
            if not isinstance(value, dict) or set(value) != {'evidence_id'}:
                raise EvidenceSelectionError('evidence_id_required', field=path)
            eid = value['evidence_id']
            if not isinstance(eid, str) or eid not in selected['spans']:
                raise EvidenceSelectionError('unknown_evidence_id', field=path, evidence_id=eid)
            used_spans.add(eid)
            span = selected['spans'][eid]
            return {'source_id': span['source_id'], 'quote': span['quote']}

        def walk(value, path):
            nonlocal node_count
            node_count+=1
            if node_count>20000 or path.count('.')>160:raise EvidenceSelectionError('selection_tree_limit',field=path[:200])
            if isinstance(value, list):
                return [walk(child, f'{path}[{i}]') for i, child in enumerate(value)]
            if not isinstance(value, dict):
                return value
            if 'quote' in value or 'source_id' in value:
                raise EvidenceSelectionError('evidence_id_required', field=path)
            if 'evidence_id' in value:
                return citation(value, path)
            node = dict(value)
            # Null optional RHS keys have no semantics. Keep the chosen RHS only;
            # genuinely conflicting values still fail strict condition validation.
            if 'operator' in node:
                node={k:v for k,v in node.items() if k not in ('value','right_parameter','number_id') or v is not None}
            if node.get('type') == 'constant' or 'number_id' in node:
                nid = node.get('number_id')
                if not isinstance(nid, str) or nid not in selected['numbers']:
                    raise EvidenceSelectionError('unknown_number_id', field=path, number_id=nid)
                if 'value' in node:
                    raise EvidenceSelectionError('model_numeric_value_not_allowed', field=path, number_id=nid)
                token = selected['numbers'][nid]
                unit = node.get('unit', 'dimensionless') if node.get('type') == 'constant' else 'dimensionless'
                if unit not in ('dimensionless', 'year', 'CNY', 'CNY/year'):
                    raise EvidenceSelectionError('invalid_selected_number_unit', field=path, number_id=nid)
                if token['conversion']=='percent' and unit!='dimensionless':
                    raise EvidenceSelectionError('selected_percent_unit_conflict',field=path,number_id=nid,unit=unit)
                # Predicate units are checked against their parameters by the existing validator.
                proof = {k: token[k] for k in ('value', 'raw_value', 'conversion')}
                proof.update(evidence=citation({'evidence_id': token['evidence_id']}, path + '.number_evidence'), unit=unit)
                proofs[(nid, unit)] = proof
                node.pop('number_id')
                node['value'] = token['value']
            elif 'operator' in node and node.get('value') not in (None, 'true', 'false'):
                raise EvidenceSelectionError('number_id_required', field=path)
            return {key: walk(child, path + '.' + key) for key, child in node.items()}

        bound = walk(formula, f'formulas[{fi}]')
        # Formula evidence remains selected by the model; constant proofs use the exact token span.
        bound['constants'] = list(proofs.values())
        result['formulas'][fi] = bound
    return result


def diagnostics(error, raw, selected, stage):
    """Small current-attempt failure evidence, never a cache or full model transcript."""
    detail = {'stage': stage, 'reason': str(error)[:180], **getattr(error, 'detail', {})}
    chosen_numbers=[];chosen_evidence=[]
    def inspect(value,path):
        if isinstance(value,list):
            for i,child in enumerate(value[:40]):inspect(child,f'{path}[{i}]')
        elif isinstance(value,dict) and path.count('.')<20:
            nid=value.get('number_id');eid=value.get('evidence_id')
            if isinstance(nid,str) and nid in selected['numbers'] and len(chosen_numbers)<6:
                token=selected['numbers'][nid];span=selected['spans'][token['evidence_id']]
                chosen_numbers.append({'field':path,'number_id':nid,'requested_unit':value.get('unit'),**token,'source':{k:span.get(k) for k in ('source_id','document_id','page')},'excerpt':span['quote'][:240]})
            if isinstance(eid,str) and eid in selected['spans'] and len(chosen_evidence)<6:
                span=selected['spans'][eid]
                chosen_evidence.append({'field':path,'evidence_id':eid,**{k:span.get(k) for k in ('source_id','document_id','page','character_range')},'excerpt':span['quote'][:240]})
            for key,child in value.items():inspect(child,path+'.'+key)
    inspect(raw,'output')
    if isinstance(raw,dict) and isinstance(raw.get('formulas'),list) and raw['formulas'] and isinstance(raw['formulas'][0],dict):
        formula=raw['formulas'][0]
        detail['variables']=[{k:v.get(k) for k in ('name','unit','source_kind','binding')} for v in formula.get('variables',[])[:6] if isinstance(v,dict)]
    if chosen_numbers:detail['selected_numbers']=chosen_numbers
    if chosen_evidence:detail['selected_evidence']=chosen_evidence
    if hasattr(error,'errors'):
        detail['schema_errors']=[{'field':list(e.get('loc',[])),'type':e.get('type')} for e in error.errors()[:6]]
    nid = detail.get('number_id'); eid = detail.get('evidence_id')
    if isinstance(nid, str) and nid in selected['numbers']:
        detail['number'] = selected['numbers'][nid]
        eid = selected['numbers'][nid]['evidence_id']
    if isinstance(eid, str) and eid in selected['spans']:
        span=selected['spans'][eid];detail['source_span']={k:span.get(k) for k in ('source_id','document_id','page','character_range')};detail['source_span']['excerpt']=span['quote'][:240]
    return detail
