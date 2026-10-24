"""Evidence-preserving syntax repairs. Never infer a formula or publish a rule."""
from copy import deepcopy
import re


def normalize(raw, units):
    value = deepcopy(raw)
    notes = []
    if not isinstance(value, dict):
        return value, notes
    for formula in value.get('formulas', []):
        if not isinstance(formula, dict):
            continue
        # A declaration that only rebinds an existing symbol to itself is not a
        # new variable. Preserve its citations and let literal validation run.
        kept=[]
        for variable in formula.get('variables', []):
            name=variable.get('name') if isinstance(variable,dict) else None
            if name in units and variable.get('binding')==name and variable.get('source_kind') in ('metric','input') and variable.get('unit')==units[name] and not variable.get('expression_tree'):
                formula.setdefault('evidence',[]).extend(e for e in variable.get('evidence',[]) if e not in formula.get('evidence',[]))
                notes.append({'kind':'redundant_existing_variable','name':name})
            else:kept.append(variable)
        formula['variables']=kept
        # Product expression variables may accidentally reuse a registry name.
        # Rename their local references without changing bindings or constants.
        def contains_parameter(node,name):
            if isinstance(node,list):return any(contains_parameter(v,name) for v in node)
            return isinstance(node,dict) and (node.get('name')==name and node.get('type')=='parameter' or node.get('parameter')==name or node.get('right_parameter')==name or any(contains_parameter(v,name) for v in node.values()))
        def rename(node,old,new):
            if isinstance(node,list):
                for child in node:rename(child,old,new)
            elif isinstance(node,dict):
                for key in ('parameter','right_parameter'):
                    if node.get(key)==old:node[key]=new
                if node.get('type')=='parameter' and node.get('name')==old:node['name']=new
                for child in node.values():rename(child,old,new)
        occupied=set(units)|{v.get('name') for v in kept}
        for variable in kept:
            old=variable.get('name')
            if old not in units or old==formula.get('metric_code') or variable.get('source_kind')!='expression' or contains_parameter(variable.get('expression_tree'),old):continue
            new='local_'+old
            if len(new)>63 or new in occupied:continue
            occupied.add(new);variable['name']=new
            for key in ('expression_tree','conditions','applicability'):rename(formula.get(key),old,new)
            for other in kept:
                if other is not variable:rename(other.get('expression_tree'),old,new)
            notes.append({'kind':'local_expression_variable_name','original':old,'name':new})
        local = {**units, **{v['name']: v['unit'] for v in formula.get('variables', [])
                           if isinstance(v, dict) and 'name' in v and 'unit' in v}}

        def binary_associative(node):
            if isinstance(node, list):
                return [binary_associative(child) for child in node]
            if not isinstance(node, dict):
                return node
            result = {key: binary_associative(child) for key, child in node.items()}
            args = result.get('args')
            if set(result) == {'type', 'args'} and result['type'] in ('add', 'multiply', 'min', 'max') and isinstance(args, list) and 2 < len(args) <= 20:
                folded = args[0]
                for child in args[1:]:
                    folded = {'type': result['type'], 'args': [folded, child]}
                notes.append({'kind': 'associative_binary_nesting', 'operator': result['type'], 'arity': len(args)})
                return folded
            return result

        formula['expression_tree'] = binary_associative(formula.get('expression_tree'))
        for variable in formula.get('variables', []):
            if isinstance(variable, dict) and variable.get('expression_tree'):
                variable['expression_tree'] = binary_associative(variable['expression_tree'])

        def comparisons(node):
            if isinstance(node, dict):
                right = node.get('value')
                left = node.get('parameter')
                if 'operator' in node:
                    for key in ('value','right_parameter'):
                        if key in node and node[key] is None:node.pop(key)
                if isinstance(right, str) and right in local and left in local and 'right_parameter' not in node and local[left] == local[right] and 'operator' in node:
                    node['right_parameter'] = node.pop('value')
                    notes.append({'kind': 'named_comparison_rhs', 'parameter': left, 'right_parameter': right})
                for child in node.values():
                    comparisons(child)
            elif isinstance(node, list):
                for child in node:
                    comparisons(child)

        comparisons(formula.get('expression_tree'))
        comparisons(formula.get('applicability'))
        comparisons(formula.get('conditions', []))
        for constant in formula.get('constants', []):
            raw_number = constant.get('raw_value', '')
            if isinstance(raw_number, str) and constant.get('unit', 'dimensionless') == 'dimensionless' and constant.get('conversion', 'identity') == 'identity' and re.fullmatch(r'\d+(?:\.\d+)?倍', raw_number):
                constant['raw_value'] = raw_number[:-1]
                notes.append({'kind': 'multiplier_suffix', 'original': raw_number})
            evidence = constant.get('evidence', {})
            token = re.sub(r'\s', '', constant.get('raw_value', ''))
            quote = re.sub(r'\s', '', evidence.get('quote', ''))
            boundary = r'\d.零〇一二两三四五六七八九十百千万'
            pattern = r'(?<![' + boundary + r'])' + re.escape(token) + r'(?![' + boundary + r'])'
            if not token or re.search(pattern, quote):
                continue
            # Only widen to an already cited formula span in the SAME source that
            # contains the old citation. Literal verification still runs afterwards.
            for wider in formula.get('evidence', []):
                full = re.sub(r'\s', '', wider.get('quote', ''))
                if quote and wider.get('source_id') == evidence.get('source_id') and quote in full and re.search(pattern, full) and re.search(r'公式|[=＝×]', full):
                    constant['evidence'] = deepcopy(wider)
                    notes.append({'kind': 'constant_evidence_same_formula', 'value': constant.get('value'), 'source_id': wider['source_id']})
                    break
    return value, notes


def validate_semantics(proposal, quoted, source_text=''):
    """Reject observed time-origin/event substitutions even with literal quotations."""
    for condition in proposal.conditions:
        if condition.parameter == 'waiting_period_passed' and '等待期' not in re.sub(r'\s', '', condition.evidence.quote):
            raise ValueError('waiting_period_semantics_not_proven')
        if condition.parameter == 'normal_payment_no_changes_no_loans' and not re.search(r'交费|缴费|贷款|变更', condition.evidence.quote):
            raise ValueError('payment_scenario_semantics_not_proven')

    def walk(node):
        if isinstance(node, dict):
            yield node
            for child in node.values():
                yield from walk(child)
        elif isinstance(node, list):
            for child in node:
                yield from walk(child)

    nodes = list(walk([proposal.expression_tree, proposal.applicability]))
    predicates = nodes + [c.model_dump(exclude_none=True) for c in proposal.conditions]
    if proposal.metric_code == 'pension_annuity' and re.search(r'保险期间(?:自|为|至)[^。；]{0,100}?(?:届满|年满\d+周岁)', source_text):
        if not any(n.get('parameter') == 'policy_year' and n.get('operator') in ('lt', 'le') and n.get('right_parameter') in ('annuity_end_year', 'maturity_year') for n in predicates):
            raise ValueError('annuity_end_scope_missing')
    if any(n.get('parameter') == 'waiting_period_passed' for n in nodes) and '等待期' not in quoted:
        raise ValueError('waiting_period_semantics_not_proven')
    relative = re.search(r'自首个.{0,12}领取日起.{0,5}(?:的)?前\d+个保单年度', quoted)
    if relative and any(n.get('parameter') == 'policy_year' and 'operator' in n and 'value' in n for n in nodes):
        raise ValueError('annuity_relative_year_origin_not_proven')
    if any(n.get('parameter') == 'benefit_anniversary_eligible' for n in nodes) or any(c.parameter == 'benefit_anniversary_eligible' for c in proposal.conditions):
        if not re.search(r'年满.{0,12}周岁后的首个.{0,6}周年日', quoted):
            raise ValueError('anniversary_eligibility_not_proven')
