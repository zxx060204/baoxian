import unittest
from uuid import uuid4

from test_company_api import test_database_url
from baoxian_api.companies import MySQLCompanyService
from baoxian_api.ingestion import ProductIngestionService


class ProductIngestionTests(unittest.TestCase):
    def setUp(self):
        self.database_url = test_database_url()
        self.service = ProductIngestionService(self.database_url)
        self.conn = MySQLCompanyService(self.database_url)._connect()
        self.cursor = self.conn.cursor()
        self.company = 'python-ingestion-' + uuid4().hex
        self.category = {'code': 'PubNewProdTypeCode_00', 'name': '人寿保险'}

    def tearDown(self):
        self.cursor.execute('SELECT id FROM company WHERE name = %s', (self.company,))
        company = self.cursor.fetchone()
        if company:
            self.cursor.execute('DELETE FROM product_index_record WHERE company_id = %s', (company['id'],))
            self.cursor.execute('DELETE FROM product WHERE company_id = %s', (company['id'],))
            self.cursor.execute('DELETE FROM company WHERE id = %s', (company['id'],))
        self.cursor.close()
        self.conn.close()

    def record(self, name, record_id=None):
        result = {'companyName': self.company, 'productName': name, 'salesStatus': 'on_sale', 'salesStatusRaw': '在售', 'sourceUrl': 'https://example.test', 'fetchedAt': '2026-11-07T05:00:00.000Z'}
        if record_id is not None: result['sourceRecordId'] = record_id
        return result

    def test_identity_rules_preserve_distinct_ids_and_do_not_merge_ambiguous(self):
        first = self.service.upsert(self.record('原名', 'R1'), self.category)
        changed = self.service.upsert(self.record('同名产品', 'R1'), self.category)
        second = self.service.upsert(self.record('同名产品', 'R2'), self.category)
        ambiguous = self.service.upsert(self.record('同名产品'), self.category)
        self.assertEqual(first['status'], 'inserted')
        self.assertEqual(changed, {'status': 'updated', 'productId': first['productId']})
        self.assertEqual(second['status'], 'inserted')
        self.assertNotEqual(first['productId'], second['productId'])
        self.assertEqual(ambiguous['status'], 'ambiguous')
        self.assertEqual({row['id'] for row in ambiguous['candidates']}, {first['productId'], second['productId']})
        self.cursor.execute('SELECT id FROM product WHERE company_id = %s', (ambiguous['incoming']['companyId'],))
        self.assertEqual(len(self.cursor.fetchall()), 2)

    def test_company_name_whitespace_is_cleaned_without_folding_case(self):
        display_name = self.company[:6].upper() + ' ' + self.company[6:]
        record = self.record('规范化名称产品', 'R1')
        record['companyName'] = '  ' + self.company[:6].upper() + '   ' + self.company[6:] + '  '
        try:
            self.service.upsert(record, self.category)
            self.cursor.execute('SELECT name FROM company WHERE name=%s', (display_name,))
            self.assertEqual(self.cursor.fetchone()['name'], display_name)
        finally:
            self.cursor.execute('SELECT id FROM company WHERE name=%s', (display_name,))
            company = self.cursor.fetchone()
            if company:
                self.cursor.execute('DELETE FROM product_index_record WHERE company_id=%s', (company['id'],))
                self.cursor.execute('DELETE FROM product WHERE company_id=%s', (company['id'],))
                self.cursor.execute('DELETE FROM company WHERE id=%s', (company['id'],))

    def test_name_normalization_keeps_existing_source_id(self):
        created = self.service.upsert(self.record('测试（A）', 'R1'), self.category)
        changed = self.service.upsert(self.record('测试(A)'), self.category)
        self.assertEqual(changed, {'status': 'updated', 'productId': created['productId']})
        self.cursor.execute('SELECT name, association_record_id FROM product WHERE id = %s', (created['productId'],))
        self.assertEqual(self.cursor.fetchone(), {'name': '测试(A)', 'association_record_id': 'R1'})

    def test_new_identifier_with_unidentified_same_name_is_ambiguous(self):
        old = self.service.upsert(self.record('同名'), self.category)
        result = self.service.upsert(self.record('同名', 'R1'), self.category)
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual([row['id'] for row in result['candidates']], [old['productId']])

    def test_updated_source_reports_differences_without_losing_confirmed_values(self):
        class Reporter:
            def __init__(self): self.diffs = []; self.source_diffs = []
            def record_correction_diff(self, *args): self.diffs.append(args)
            def record_source_value_change(self, *args): self.source_diffs.append(args)
        reporter = Reporter()
        service = ProductIngestionService(self.database_url, reporter)
        initial = self.record('原名', 'R1')
        initial['saleDateRaw'] = '2026-01-02'
        item = service.upsert(initial, self.category)
        self.cursor.execute("UPDATE product SET confirmed_name = '确认名', confirmed_sales_status = 'off_sale' WHERE id = %s", (item['productId'],))
        changed = self.record('新名', 'R1')
        changed['salesStatusRaw'] = '停用'
        changed['salesStatus'] = 'disabled'
        service.upsert(changed, self.category, 123)
        self.cursor.execute('SELECT confirmed_name, confirmed_sales_status, sale_date_raw, previous_sales_status_raw FROM product WHERE id = %s', (item['productId'],))
        self.assertEqual(self.cursor.fetchone(), {'confirmed_name': '确认名', 'confirmed_sales_status': 'off_sale', 'sale_date_raw': '2026-01-02', 'previous_sales_status_raw': '在售'})
        self.assertEqual(set(reporter.diffs), {(123, item['productId'], 'name', '确认名', '新名')})
        self.assertEqual(reporter.source_diffs, [(123, item['productId'], '在售', '停用')])

    def test_stopped_records_share_product_while_on_sale_records_remain_distinct(self):
        def record(status, raw, record_id, sale_date):
            value = self.record('同名保障产品', record_id)
            value.update({'salesStatus': status, 'salesStatusRaw': raw, 'saleDateRaw': sale_date})
            return value

        first = self.service.upsert(record('disabled', '停用', 'S1', '2020-01-01'), self.category)
        second = self.service.upsert(record('off_sale', '停售', 'S2', '2022-01-01'), self.category)
        repeated = self.service.upsert(record('disabled', '停用', 'S1', '2020-01-01'), self.category)
        active = self.service.upsert(record('on_sale', '在售', 'A1', '2025-01-01'), self.category)
        active_again = self.service.upsert(record('on_sale', '在售', 'A2', '2026-01-01'), self.category)

        self.assertEqual(first['productId'], second['productId'])
        self.assertEqual(first['productId'], repeated['productId'])
        self.assertNotEqual(first['productId'], active['productId'])
        self.assertNotEqual(active['productId'], active_again['productId'])
        self.cursor.execute('SELECT identity_group,sales_status,sales_status_raw,same_name_count,association_record_id,sale_date_raw FROM product WHERE id=%s', (first['productId'],))
        self.assertEqual(self.cursor.fetchone(), {'identity_group': 'stopped', 'sales_status': 'stopped', 'sales_status_raw': '停售/停用', 'same_name_count': 4, 'association_record_id': None, 'sale_date_raw': None})
        self.cursor.execute('SELECT association_record_id,sales_status,sales_status_raw,sale_date_raw FROM product_index_record WHERE product_id=%s ORDER BY association_record_id', (first['productId'],))
        self.assertEqual(self.cursor.fetchall(), [
            {'association_record_id': 'S1', 'sales_status': 'disabled', 'sales_status_raw': '停用', 'sale_date_raw': '2020-01-01'},
            {'association_record_id': 'S2', 'sales_status': 'off_sale', 'sales_status_raw': '停售', 'sale_date_raw': '2022-01-01'},
        ])
        self.cursor.execute('SELECT same_name_count FROM product WHERE company_id=(SELECT id FROM company WHERE name=%s) AND normalized_name=%s', (self.company, '同名保障产品'))
        self.assertEqual([row['same_name_count'] for row in self.cursor.fetchall()], [4, 4, 4])

    def test_status_changes_move_only_the_matching_association_record_between_products(self):
        def record(status, raw, record_id):
            value = self.record('状态变更产品', record_id)
            value.update({'salesStatus': status, 'salesStatusRaw': raw})
            return value

        active_one = self.service.upsert(record('on_sale', '在售', 'A1'), self.category)
        active_two = self.service.upsert(record('on_sale', '在售', 'A2'), self.category)
        stopped = self.service.upsert(record('disabled', '停用', 'S1'), self.category)
        moved_to_stopped = self.service.upsert(record('off_sale', '停售', 'A1'), self.category)
        self.assertEqual(moved_to_stopped['productId'], stopped['productId'])
        self.assertNotEqual(moved_to_stopped['productId'], active_one['productId'])
        self.cursor.execute('SELECT id,identity_group,same_name_count FROM product WHERE company_id=(SELECT id FROM company WHERE name=%s) AND normalized_name=%s ORDER BY id', (self.company, '状态变更产品'))
        rows = self.cursor.fetchall()
        self.assertEqual(sorted((row['identity_group'], row['same_name_count']) for row in rows), [('individual', 3), ('stopped', 3)])

        moved_back_to_sale = self.service.upsert(record('on_sale', '在售', 'S1'), self.category)
        self.assertNotEqual(moved_back_to_sale['productId'], stopped['productId'])
        self.assertNotEqual(moved_back_to_sale['productId'], active_two['productId'])
        self.cursor.execute('SELECT id,identity_group,same_name_count FROM product WHERE company_id=(SELECT id FROM company WHERE name=%s) AND normalized_name=%s ORDER BY id', (self.company, '状态变更产品'))
        rows = self.cursor.fetchall()
        self.assertEqual(sorted(row['identity_group'] for row in rows), ['individual', 'individual', 'stopped'])
        self.assertEqual({row['same_name_count'] for row in rows}, {3})
