import json
import unittest

from baoxian_api.benefit_illustration import (
    BenefitDataMissingError,
    BenefitIllustrationService,
    ParameterOutOfRangeError,
    UnsupportedProductTemplateError,
    build_endowment_illustration,
    CashValueTable,
    RateTable,
    death_ratio,
    parse_cash_grid_layout,
    parse_cash_text_lines,
    parse_rate_layout,
)


def cell(text, rowspan=1, colspan=1):
    return [text, rowspan, colspan]


def rate_fixture():
    return {
        'contexts': [
            {'text': '《X两全保险》费率表\n（年交千元保险费对应基本保险金额）', 'page': 1, 'bbox': [200, 55, 393, 78]},
            {'text': '保至70岁', 'page': 1, 'bbox': [274, 775, 319, 786]},
        ],
        'tables': [
            {'page': 1, 'bbox': [50, 100, 545, 700], 'grid': [
                [cell('保至60岁', 1, 9), -1, -1, -1, -1, -1, -1, -1, -1],
                [cell('交费期间\n投保年龄', 2, 1), cell('男性', 1, 4), -1, -1, -1, cell('女性', 1, 4), -1, -1, -1],
                [-1, cell('趸交'), cell('3年交'), cell('5年交'), cell('10年交'), cell('趸交'), cell('3年交'), cell('5年交'), cell('10年交')],
                [cell('0'), cell('4003.03'), cell('11775.62'), cell('19259.35'), cell('36760.22'), cell('4003.37'), cell('11776.94'), cell('19262.07'), cell('36768.21')],
                [cell('40'), cell('1500.00'), cell('4400.00'), cell('7200.00'), cell('13700.00'), cell('1510.00'), cell('4430.00'), cell('7250.00'), cell('13800.00')],
            ]},
            {'page': 2, 'bbox': [50, 100, 545, 700], 'grid': [
                [cell('交费期间\n投保年龄', 2, 1), cell('男性', 1, 4), -1, -1, -1, cell('女性', 1, 4), -1, -1, -1],
                [-1, cell('趸交'), cell('3年交'), cell('5年交'), cell('10年交'), cell('趸交'), cell('3年交'), cell('5年交'), cell('10年交')],
                [cell('40'), cell('3097.44'), cell('9108.80'), cell('14891.43'), cell('28396.30'), cell('3114.45'), cell('9160.51'), cell('14979.62'), cell('28580.40')],
            ]},
            {'page': 3, 'bbox': [50, 100, 545, 300], 'grid': [
                [cell('57'), cell('1233.35'), cell('3629.73'), cell('5940.71'), cell(''), cell('1244.26'), cell('3660.66'), cell('5988.50'), cell('')],
            ]},
        ],
    }


class TestRateLayout(unittest.TestCase):
    def setUp(self):
        self.table = parse_rate_layout(rate_fixture())

    def test_in_grid_title_section(self):
        self.assertEqual(self.table.lookup('保至60岁', '男性', '3年交', 0), 11775.62)
        self.assertEqual(self.table.lookup('保至60岁', '女性', '10年交', 40), 13800.00)

    def test_context_marker_section(self):
        self.assertEqual(self.table.lookup('保至70岁', '男性', '3年交', 40), 9108.80)

    def test_continuation_rows_join_previous_section(self):
        self.assertEqual(self.table.lookup('保至70岁', '男性', '5年交', 57), 5940.71)

    def test_empty_cell_is_missing(self):
        self.assertIsNone(self.table.lookup('保至70岁', '男性', '10年交', 57))

    def test_unknown_age(self):
        self.assertIsNone(self.table.lookup('保至60岁', '男性', '趸交', 99))


class TestCashParsing(unittest.TestCase):
    def test_grid_with_merged_cells(self):
        layout = {'tables': [{'grid': [
            [cell('保险期间'), cell('交费期间'), cell('性别'), cell('年龄'), cell('保单年度'), cell('现金价值')],
            [cell('8年', 3, 1), cell('趸交', 3, 1), cell('男性', 3, 1), cell('0'), cell('1'), cell('897.55')],
            [-1, -1, -1, cell('0'), cell('2'), cell('924.43')],
            [-1, -1, -1, cell('0'), cell('3'), cell('952.15')],
        ]}]}
        table = parse_cash_grid_layout(layout)
        self.assertEqual(table.lookup('8年', '趸交', '男性', 0, 2), 924.43)
        self.assertIsNone(table.lookup('8年', '趸交', '男性', 0, 4))

    def test_full_text_lines(self):
        lines = [
            '《国民福寿双全A05款两全保险》现金价值全表（每1000元保费）',
            '保险期间 交费期间 性别 年龄 保单年度 现金价值',
            '保至60岁 趸交 男性 0 1 830.70',
            '保至90岁 3年交 男性 40 1 664.99',
            '保至90岁 3年交 男性 40 2 1,597.22',
            '无效行',
        ]
        table = parse_cash_text_lines(lines)
        self.assertEqual(table.lookup('保至60岁', '趸交', '男性', 0, 1), 830.70)
        self.assertEqual(table.lookup('保至90岁', '3年交', '男性', 40, 2), 1597.22)
        self.assertIsNone(table.lookup('保至90岁', '3年交', '女性', 40, 1))


class TestDeathRatio(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(death_ratio(0), 1.0)
        self.assertEqual(death_ratio(17), 1.0)
        self.assertEqual(death_ratio(18), 1.6)
        self.assertEqual(death_ratio(40), 1.6)
        self.assertEqual(death_ratio(41), 1.4)
        self.assertEqual(death_ratio(60), 1.4)
        self.assertEqual(death_ratio(61), 1.2)
        self.assertEqual(death_ratio(90), 1.2)


def a05_demo_tables():
    rate = RateTable({('保至90岁', '男性', '3年交', 40): 9108.80})
    cash_units = {1: 664.99, 2: 1597.22, 3: 2665.29, 4: 2782.76, 5: 2905.53, 6: 3033.85, 7: 3168.01, 8: 3244.92}
    entries = {}
    for year in range(1, 51):
        entries[('保至90岁', '3年交', '男性', 40, year)] = cash_units.get(year, 3300.0 + year * 60.0 if year < 50 else 0.0)
    return rate, CashValueTable(entries)


class TestEndowmentIllustration(unittest.TestCase):
    def test_matches_a05_prospectus_demo(self):
        rate, cash = a05_demo_tables()
        result = build_endowment_illustration(rate, cash, age=40, gender='male', coverage='to_90', payment='3', annual_premium=10000)
        self.assertEqual(result['sumAssured'], 91088)
        rows = result['rows']
        self.assertEqual(len(rows), 50)
        expected_cash = [6650, 15972, 26653, 27828, 29055, 30339, 31680, 32449]
        expected_death = [16000, 28000, 42000, 42000, 42000, 42000, 42000, 42000]
        expected_annual = [10000, 10000, 10000, 0, 0, 0, 0, 0]
        for index in range(8):
            row = rows[index]
            self.assertEqual(row['policyYear'], index + 1)
            self.assertEqual(row['age'], 41 + index)
            self.assertEqual(row['annualPremium'], expected_annual[index])
            self.assertEqual(row['cumulativePremium'], min(index + 1, 3) * 10000)
            self.assertEqual(row['cashValue'], expected_cash[index])
            self.assertEqual(row['deathBenefit'], expected_death[index])
            self.assertEqual(row['maturityBenefit'], 0)
        last = rows[-1]
        self.assertEqual(last['policyYear'], 50)
        self.assertEqual(last['age'], 90)
        self.assertEqual(last['maturityBenefit'], 91088)
        self.assertEqual(last['cashValue'], 0)
        self.assertEqual(last['deathBenefit'], 36000)

    def test_age_beyond_coverage_rejected(self):
        rate, cash = a05_demo_tables()
        with self.assertRaises(ParameterOutOfRangeError):
            build_endowment_illustration(rate, cash, age=60, gender='male', coverage='to_60', payment='3', annual_premium=10000)

    def test_missing_rate_entry_rejected(self):
        rate, cash = a05_demo_tables()
        with self.assertRaises(ParameterOutOfRangeError):
            build_endowment_illustration(rate, cash, age=41, gender='male', coverage='to_90', payment='3', annual_premium=10000)

    def test_missing_cash_entry_rejected(self):
        rate = RateTable({('保至90岁', '男性', '3年交', 40): 9108.80})
        with self.assertRaises(ParameterOutOfRangeError):
            build_endowment_illustration(rate, CashValueTable({}), age=40, gender='male', coverage='to_90', payment='3', annual_premium=10000)


class FakeDocumentService:
    def __init__(self, name='国民福寿双全A05款两全保险', rate_body=None, cash_body=None, cash_file=None):
        self.name = name
        self.rate_body = rate_body
        self.cash_body = cash_body
        self.cash_file = cash_file
        self.product = object()

    def _query(self, database, sql, args):
        return [{'name': self.name}]

    async def _current_layout_documents(self, product_id):
        rows = []
        if self.rate_body is not None:
            rows.append({'id': 10, 'type': 'rate_table', 'sha256': 'r'})
        if self.cash_body is not None:
            rows.append({'id': 12, 'type': 'cash_value_table', 'sha256': 'c'})
        return rows

    async def get_saved_layout(self, document_id):
        return self.rate_body if document_id == 10 else self.cash_body

    async def get_file(self, document_id):
        return {'body': self.cash_file}


def cash_grid_body():
    rows = [[cell('保险期间'), cell('交费期间'), cell('性别'), cell('年龄'), cell('保单年度'), cell('现金价值')]]
    for year in range(1, 51):
        rows.append([cell('保至90岁'), cell('3年交'), cell('男性'), cell('40'), cell(str(year)), cell(str(664.99 if year == 1 else 0.0))])
    return json.dumps({'tables': [{'grid': rows}]}, ensure_ascii=False).encode('utf-8')


class TestBenefitService(unittest.IsolatedAsyncioTestCase):
    async def test_generate(self):
        service = BenefitIllustrationService(FakeDocumentService(
            rate_body=json.dumps({
                'tables': [{'grid': [
                    [cell('保至90岁', 1, 9), -1, -1, -1, -1, -1, -1, -1, -1],
                    [cell('交费期间\n投保年龄', 2, 1), cell('男性', 1, 4), -1, -1, -1, cell('女性', 1, 4), -1, -1, -1],
                    [-1, cell('趸交'), cell('3年交'), cell('5年交'), cell('10年交'), cell('趸交'), cell('3年交'), cell('5年交'), cell('10年交')],
                    [cell('40'), cell('3097.44'), cell('9108.80'), cell('14891.43'), cell('28396.30'), cell('3114.45'), cell('9160.51'), cell('14979.62'), cell('28580.40')],
                ]}],
            }).encode('utf-8'),
            cash_body=cash_grid_body(),
        ))
        result = await service.generate(product_id=13, age=40, gender='male', coverage='to_90', payment='3', annual_premium=10000)
        self.assertEqual(result['productName'], '国民福寿双全A05款两全保险')
        self.assertEqual(result['sumAssured'], 91088)
        self.assertEqual(len(result['rows']), 50)
        self.assertEqual(result['rows'][0]['cashValue'], 6650)

    async def test_participating_product_rejected(self):
        service = BenefitIllustrationService(FakeDocumentService(name='国民福寿双全B01款两全保险（分红型）'))
        with self.assertRaises(UnsupportedProductTemplateError):
            await service.generate(product_id=14, age=40, gender='male', coverage='to_90', payment='3', annual_premium=10000)

    async def test_missing_documents(self):
        service = BenefitIllustrationService(FakeDocumentService())
        with self.assertRaises(BenefitDataMissingError):
            await service.generate(product_id=13, age=40, gender='male', coverage='to_90', payment='3', annual_premium=10000)


if __name__ == '__main__':
    unittest.main()
