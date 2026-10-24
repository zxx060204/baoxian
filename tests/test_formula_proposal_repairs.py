import copy
import unittest
from insurance_rag.illustration.preparation import validate_proposal
from insurance_rag.illustration.schema import standard_metrics


class FormulaProposalRepairs(unittest.TestCase):
    def packet(self, text, tree=None):
        proof = {'source_id': 's', 'quote': text}
        raw = {'formulas': [{'metric_code': 'pension_annuity', 'summary': '默认年领', 'evidence': [proof],
                             'expression_tree': tree or {'type': 'parameter', 'name': 'base_amount'},
                             'time_basis': '约定领取日', 'complete': True}]}
        return raw, [{'source_id': 's', 'text': text, 'source': {'document_version_id': 'd', 'page_number': 1}}]

    def validate(self, raw, sources):
        return validate_proposal(raw, sources, 'pension_annuity', metric_units={'pension_annuity': 'CNY', 'base_amount': 'CNY'})

    def test_multiplier_normalization_retains_evidence_and_does_not_mutate_output(self):
        raw, sources = self.packet('每年按基本保险金额的1.2倍给付养老年金。', {'type': 'multiply', 'args': [{'type': 'parameter', 'name': 'base_amount'}, {'type': 'constant', 'value': '1.2'}]})
        raw['formulas'][0]['constants'] = [{'value': '1.2', 'raw_value': '1.2倍', 'evidence': raw['formulas'][0]['evidence'][0]}]
        before = copy.deepcopy(raw)
        result = self.validate(raw, sources)
        self.assertEqual(raw, before)
        self.assertEqual(result['normalization_notes'][0]['kind'], 'multiplier_suffix')
        raw['formulas'][0]['constants'][0]['value'] = '2.1'
        with self.assertRaisesRegex(ValueError, 'constant_conversion_mismatch'):
            self.validate(raw, sources)

    def test_invalid_model_lists_fail_as_validation_errors(self):
        raw, sources = self.packet('每年按基本保险金额给付养老年金。')
        raw['formulas'][0]['constants'] = None
        with self.assertRaises(ValueError):
            self.validate(raw, sources)

    def test_evidence_can_only_widen_inside_same_cited_formula(self):
        raw, sources = self.packet('计算公式为：养老年金=基本保险金额×1，其中n为保单年度数。', {'type': 'multiply', 'args': [{'type': 'parameter', 'name': 'base_amount'}, {'type': 'constant', 'value': '1'}]})
        raw['formulas'][0]['constants'] = [{'value': '1', 'raw_value': '1', 'evidence': {'source_id': 's', 'quote': '其中n为保单年度数'}}]
        self.assertEqual(self.validate(raw, sources)['normalization_notes'][0]['kind'], 'constant_evidence_same_formula')
        raw['formulas'][0]['constants'][0]['raw_value'] = '2'
        with self.assertRaisesRegex(ValueError, 'constant_not_literal'):
            self.validate(raw, sources)

    def test_named_variable_rhs_is_repaired_but_unknown_names_are_rejected(self):
        raw, sources = self.packet('自约定领取日起按基本保险金额给付养老年金。')
        raw['formulas'][0]['applicability'] = {'type': 'compare', 'parameter': 'policy_year', 'operator': 'ge', 'value': 'annuity_start_year'}
        result = self.validate(raw, sources)
        self.assertEqual(result['applicability']['right_parameter'], 'annuity_start_year')
        raw['formulas'][0]['applicability']['value'] = 'unknown_start_year'
        with self.assertRaisesRegex(ValueError, 'invalid_condition_number'):
            self.validate(raw, sources)

    def test_survival_cannot_prove_waiting_period(self):
        raw, sources = self.packet('自首个领取日起，在领取日生存按基本保险金额给付养老年金。')
        raw['formulas'][0]['conditions'] = [{'parameter': 'waiting_period_passed', 'operator': 'eq', 'value': 'true', 'evidence': raw['formulas'][0]['evidence'][0]}]
        with self.assertRaisesRegex(ValueError, 'waiting_period_semantics_not_proven'):
            self.validate(raw, sources)

    def test_policy_year_cannot_replace_years_since_first_receipt(self):
        raw, sources = self.packet('自首个养老保险金领取日起的前10个保单年度按基本保险金额给付养老年金。', {'type': 'piecewise', 'branches': [{'condition': {'type': 'compare', 'parameter': 'policy_year', 'operator': 'le', 'value': '10'}, 'value': {'type': 'parameter', 'name': 'base_amount'}}], 'otherwise': {'type': 'parameter', 'name': 'base_amount'}})
        with self.assertRaisesRegex(ValueError, 'annuity_relative_year_origin_not_proven'):
            self.validate(raw, sources)

    def test_annuity_end_is_required_for_fixed_term_but_retirement_age_is_not_an_end(self):
        raw, sources = self.packet('保险期间自生效日起至年满106周岁时止。每年按基本保险金额给付养老年金。')
        with self.assertRaisesRegex(ValueError, 'annuity_end_scope_missing'):
            self.validate(raw, sources)
        sources[0]['text'] = '保险期间为被保险人终身。养老年金领取年龄男性不得早于60周岁。每年按基本保险金额给付养老年金。'
        raw['formulas'][0]['evidence'][0]['quote'] = '每年按基本保险金额给付养老年金。'
        self.assertEqual(self.validate(raw, sources)['dependencies'], ['base_amount'])

    def test_survival_cannot_prove_payment_scenario(self):
        raw, sources = self.packet('领取日生存按基本保险金额给付养老年金。')
        raw['formulas'][0]['conditions'] = [{'parameter': 'normal_payment_no_changes_no_loans', 'operator': 'eq', 'value': 'true', 'evidence': raw['formulas'][0]['evidence'][0]}]
        with self.assertRaisesRegex(ValueError, 'payment_scenario_semantics_not_proven'):
            self.validate(raw, sources)




if __name__ == '__main__':
    unittest.main()
