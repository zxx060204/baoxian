"""Explicit user condition types; backend/release identifiers are never conditions."""

AXIS_FIELDS = frozenset({'age', 'sex', 'policy_year', 'payment_period', 'coverage_period',
    'payment_frequency', 'insured_count', 'second_age', 'second_sex', 'plan',
    'basic_medical_insurance', 'enrollment_window', 'underwriting_class',
    'benefit', 'premium_component', 'waiting_period_category', 'waiting_period_waiver_confirmed',
    'benefit_paid', 'accident_occurred', 'family_policy', 'family_member_count'})
CONTROL_FIELDS = frozenset({'value_role', 'unit', 'conditions', 'schema_id'})
CONDITION_FIELDS = AXIS_FIELDS | CONTROL_FIELDS

def validate_conditions(value, *, model=False):
    allowed = CONDITION_FIELDS - {'schema_id'} if model else CONDITION_FIELDS
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('invalid_agent_conditions')
    for key, item in value.items():
        if item is None:
            continue
        if key in {'age', 'policy_year', 'second_age'}:
            if key == 'second_age' and item == 'not_applicable':
                continue
            if type(item) is not int or not 0 <= item <= (150 if key != 'policy_year' else 200):
                raise ValueError('invalid_agent_' + key)
        elif key in {'sex', 'second_sex'}:
            if item not in ('male', 'female', 'unisex', 'not_applicable') or (key == 'sex' and item == 'not_applicable'):
                raise ValueError('invalid_agent_' + key)
        elif key in {'basic_medical_insurance','waiting_period_waiver_confirmed','benefit_paid','accident_occurred','family_policy'}:
            if type(item) is not bool:
                raise ValueError('invalid_agent_' + key)
        elif key == 'family_member_count':
            if type(item) is not int or not 1<=item<=100:raise ValueError('invalid_agent_family_member_count')
        elif key == 'waiting_period_category':
            if item not in ('with_waiting_period','without_waiting_period'):raise ValueError('invalid_agent_waiting_period_category')
        elif key == 'insured_count':
            if type(item) is not int or item not in (1, 2):
                raise ValueError('invalid_agent_' + key)
        elif key == 'enrollment_window':
            if item not in ('initial_or_after_60_days', 'within_60_days'):
                raise ValueError('invalid_agent_' + key)
        elif key == 'premium_component':
            if item not in ('first', 'additional', 'standard'):
                raise ValueError('invalid_agent_' + key)
        elif key in {'payment_period', 'coverage_period', 'unit'}:
            if not isinstance(item, dict) or not item:
                raise ValueError('invalid_agent_' + key)
        elif key == 'conditions':
            if not isinstance(item, list) or any(not isinstance(v, str) or not v.strip() for v in item):
                raise ValueError('invalid_agent_conditions_list')
        elif not isinstance(item, str) or not item.strip() or len(item) > 200:
            raise ValueError('invalid_agent_' + key)
    # Reject non-JSON values, booleans used as numeric axes, and nested nonfinite numbers.
    import json
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError('invalid_agent_condition_value') from None
    return value
