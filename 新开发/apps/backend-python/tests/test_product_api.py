import unittest
import json
from uuid import uuid4
import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from test_company_api import test_database_url
from baoxian_api.app import create_app
from baoxian_api.products import MySQLProductService
from baoxian_api.companies import MySQLCompanyService


def test_crawl_url():
    value = os.environ.get("TEST_CRAWL_DATABASE_URL")
    if not value:
        for line in (Path(__file__).resolve().parents[3] / ".env").read_text(encoding="utf-8").splitlines():
            key, sep, item = line.partition("=")
            if sep and key.strip() == "TEST_CRAWL_DATABASE_URL":
                value = item.strip().strip("\"'")
                break
    if not value or urlparse(value).path != "/baoxian_crawl_test":
        raise RuntimeError("采集集成测试只允许使用 baoxian_crawl_test")
    return value


class ProductApiTests(unittest.TestCase):
    def setUp(self):
        self.service = MySQLProductService(test_database_url(), test_crawl_url())
        self.crawl = MySQLCompanyService(test_crawl_url())._connect()
        self.crawl_cursor = self.crawl.cursor()
        self.name = "python-product-" + uuid4().hex
        self.connection = self.service._connect()
        self.cursor = self.connection.cursor()
        self.cursor.execute("INSERT INTO company (name) VALUES (%s)", (self.name,))
        self.company_id = self.cursor.lastrowid
        self.cursor.execute("INSERT INTO product (company_id, name, normalized_name, sales_status, sales_status_raw, status_fetched_at, source_url, category_code, category_name) VALUES (%s,%s,%s,%s,%s,UTC_TIMESTAMP(),%s,%s,%s)", (self.company_id, self.name, self.name, 'on_sale', '在售', 'https://example.com', 'PubNewProdTypeCode_00', '人寿保险'))
        self.product_id = self.cursor.lastrowid
        self.client = TestClient(create_app(internal_api_token="token", product_service=self.service))

    def tearDown(self):
        self.client.close()
        self.cursor.execute("SELECT id FROM document WHERE product_id = %s", (self.product_id,))
        for document in self.cursor.fetchall():
            self.crawl_cursor.execute("DELETE FROM document_snapshot WHERE document_id = %s", (document['id'],))
        self.crawl_cursor.execute("DELETE FROM product_document_status WHERE product_id = %s", (self.product_id,))
        self.crawl_cursor.close()
        self.crawl.close()
        self.cursor.execute("DELETE FROM product_index_record WHERE company_id = %s", (self.company_id,))
        self.cursor.execute("DELETE FROM document WHERE product_id = %s", (self.product_id,))
        self.cursor.execute("DELETE FROM product WHERE id = %s AND company_id = %s", (self.product_id, self.company_id))
        self.cursor.execute("DELETE FROM company WHERE id = %s AND name = %s", (self.company_id, self.name))
        self.cursor.close()
        self.connection.close()

    def test_index_overview_reads_actual_product_and_progress_tables(self):
        from baoxian_api.tasks import MySQLTaskService
        self.cursor.execute("SELECT COUNT(*) AS n FROM product WHERE category_code='PubNewProdTypeCode_00'")
        expected = self.cursor.fetchone()['n']
        with TestClient(create_app(internal_api_token='token', product_service=self.service, task_service=MySQLTaskService(test_crawl_url()))) as client:
            response = client.get('/api/index-overview', headers={'Authorization': 'Bearer token'})
        self.assertEqual(response.status_code, 200)
        row = response.json()['items'][0]
        self.assertEqual((row['code'], row['productCount']), ('PubNewProdTypeCode_00', expected))
        self.assertIn(row['status'], ('running', 'queued', 'completed', 'incomplete', 'not_started'))

    def test_review_diff_adopt_and_keep(self):
        from baoxian_api.review_reporter import MySQLReviewReporter
        reporter = MySQLReviewReporter(test_crawl_url(), test_database_url())
        task_id = int(uuid4().int % 1000000000000) + 1000000000000
        name_review = reporter.record_correction_diff(task_id, self.product_id, 'name', '旧名称', '来源新名称')
        status_review = reporter.record_source_value_change(task_id, self.product_id, '在售', '停售')
        try:
            with TestClient(create_app(internal_api_token='token', review_service=reporter)) as client:
                headers = {'Authorization': 'Bearer token'}
                adopted = client.post(f"/api/review-items/{name_review['id']}/resolve", headers=headers, json={'action': 'adopt'})
                self.assertEqual(adopted.status_code, 200)
                self.assertEqual((adopted.json()['item']['resolvedProductId'], adopted.json()['product']['confirmedName']), (self.product_id, '来源新名称'))
                status = client.post(f"/api/review-items/{status_review['id']}/resolve", headers=headers, json={'action': 'adopt'})
                self.assertEqual(status.json()['product']['confirmedSalesStatus'], 'off_sale')
                keep_review = reporter.record_source_value_change(task_id, self.product_id, '停售', '停用')
                kept = client.post(f"/api/review-items/{keep_review['id']}/resolve", headers=headers, json={'action': 'keep'})
                self.assertEqual(kept.json()['product']['confirmedSalesStatus'], 'off_sale')
                self.assertEqual(client.post(f"/api/review-items/{keep_review['id']}/resolve", headers=headers, json={'action': 'keep'}).status_code, 409)
        finally:
            self.crawl_cursor.execute('DELETE FROM review_item WHERE task_id=%s', (task_id,))

    def test_review_identity_merge_and_create(self):
        from baoxian_api.review_reporter import MySQLReviewReporter
        reporter = MySQLReviewReporter(test_crawl_url(), test_database_url())
        task_id = int(uuid4().int % 1000000000000) + 1000000000000
        incoming = {'companyId': self.company_id, 'companyName': self.name, 'name': self.name, 'normalizedName': self.name, 'salesStatus': 'off_sale', 'salesStatusRaw': '停售', 'fetchedAt': '2026-01-01T00:00:00.000Z', 'sourceUrl': 'https://example.test/source', 'category': {'code': 'test', 'name': '测试'}}
        first = reporter.record_product_identity(task_id, incoming, [{'id': self.product_id}])
        second = reporter.record_product_identity(task_id, incoming, [{'id': self.product_id}])
        created_id = None
        try:
            with TestClient(create_app(internal_api_token='token', review_service=reporter)) as client:
                headers = {'Authorization': 'Bearer token'}
                url = f"/api/review-items/{first['id']}/resolve"
                invalid = client.post(url, headers=headers, json={'action': 'merge', 'productId': self.product_id + 1000000})
                self.assertEqual((invalid.status_code, invalid.json()['code']), (400, 'invalid_resolution'))
                merged = client.post(url, headers=headers, json={'action': 'merge', 'productId': self.product_id})
                self.assertEqual((merged.status_code, merged.json()['product']['id'], merged.json()['product']['salesStatus']), (200, self.product_id, 'stopped'))
                self.assertEqual(client.post(url, headers=headers, json={'action': 'merge', 'productId': self.product_id}).status_code, 409)
                created = client.post(f"/api/review-items/{second['id']}/resolve", headers=headers, json={'action': 'new'})
                self.assertEqual(created.status_code, 200)
                created_id = created.json()['product']['id']
                self.assertNotEqual(created_id, self.product_id)
                self.assertEqual(created.json()['item']['resolvedProductId'], created_id)
        finally:
            self.crawl_cursor.execute('DELETE FROM review_item WHERE task_id=%s', (task_id,))
            self.cursor.execute('DELETE FROM product_index_record WHERE company_id=%s AND product_id<>%s', (self.company_id, self.product_id))
            self.cursor.execute('DELETE FROM product WHERE company_id=%s AND id<>%s', (self.company_id, self.product_id))
            if created_id: self.cursor.execute('DELETE FROM product WHERE id=%s AND company_id=%s', (created_id, self.company_id))

    def test_identity_new_rolls_back_product_when_review_update_fails(self):
        from unittest.mock import patch
        from baoxian_api.review_reporter import MySQLReviewReporter
        from baoxian_api.tasks import public_row
        reporter = MySQLReviewReporter(test_crawl_url(), test_database_url())
        task_id = int(uuid4().int % 1000000000000) + 1000000000000
        incoming = {'companyId': self.company_id, 'companyName': self.name, 'name': self.name, 'normalizedName': self.name, 'salesStatus': 'on_sale', 'salesStatusRaw': '在售', 'fetchedAt': '2026-01-01T00:00:00Z', 'sourceUrl': 'https://example.test/source', 'category': {'code': 'test', 'name': '测试'}}
        item = reporter.record_product_identity(task_id, incoming, [{'id': self.product_id}])
        def fail_after_product_write(row):
            if 'resolution' in row and row['resolution'] == 'new': raise RuntimeError('故障注入')
            return public_row(row)
        try:
            with patch('baoxian_api.review_reporter.public_row', side_effect=fail_after_product_write):
                with TestClient(create_app(internal_api_token='token', review_service=reporter), raise_server_exceptions=False) as client:
                    failed = client.post(f"/api/review-items/{item['id']}/resolve", headers={'Authorization': 'Bearer token'}, json={'action': 'new'})
                    self.assertEqual(failed.status_code, 500)
            self.cursor.execute('SELECT COUNT(*) AS n FROM product WHERE company_id=%s', (self.company_id,))
            self.assertEqual(self.cursor.fetchone()['n'], 1)
            self.crawl_cursor.execute('SELECT status FROM review_item WHERE id=%s', (item['id'],))
            self.assertEqual(self.crawl_cursor.fetchone()['status'], 'pending')
            with TestClient(create_app(internal_api_token='token', review_service=reporter)) as client:
                retried = client.post(f"/api/review-items/{item['id']}/resolve", headers={'Authorization': 'Bearer token'}, json={'action': 'new'})
                self.assertEqual(retried.status_code, 200)
            self.cursor.execute('SELECT COUNT(*) AS n FROM product WHERE company_id=%s', (self.company_id,))
            self.assertEqual(self.cursor.fetchone()['n'], 2)
        finally:
            self.crawl_cursor.execute('DELETE FROM review_item WHERE task_id=%s', (task_id,))
            self.cursor.execute('DELETE FROM product_index_record WHERE company_id=%s AND product_id<>%s', (self.company_id, self.product_id))
            self.cursor.execute('DELETE FROM product WHERE company_id=%s AND id<>%s', (self.company_id, self.product_id))

    def test_review_adopt_rolls_back_product_when_review_write_fails(self):
        from unittest.mock import patch
        from baoxian_api.review_reporter import MySQLReviewReporter
        from baoxian_api.tasks import public_row
        reporter = MySQLReviewReporter(test_crawl_url(), test_database_url())
        task_id = int(uuid4().int % 1000000000000) + 1000000000000
        item = reporter.record_correction_diff(task_id, self.product_id, 'name', '旧', '新')
        def fail(row):
            if 'resolution' in row and row['resolution'] == 'adopt': raise RuntimeError('故障注入')
            return public_row(row)
        try:
            with patch('baoxian_api.review_reporter.public_row', side_effect=fail):
                with TestClient(create_app(internal_api_token='token', review_service=reporter), raise_server_exceptions=False) as client:
                    response = client.post(f"/api/review-items/{item['id']}/resolve", headers={'Authorization': 'Bearer token'}, json={'action': 'adopt'})
                    self.assertEqual(response.status_code, 500)
            self.cursor.execute('SELECT confirmed_name FROM product WHERE id=%s', (self.product_id,))
            self.assertIsNone(self.cursor.fetchone()['confirmed_name'])
            self.crawl_cursor.execute('SELECT status FROM review_item WHERE id=%s', (item['id'],))
            self.assertEqual(self.crawl_cursor.fetchone()['status'], 'pending')
        finally:
            self.crawl_cursor.execute('DELETE FROM review_item WHERE task_id=%s', (task_id,))

    def test_list_products_by_company(self):
        response = self.client.get('/api/products', params={'companyId': self.company_id}, headers={'Authorization': 'Bearer token'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['items']), 1)
        item = response.json()['items'][0]
        self.assertEqual((item['id'], item['companyId'], item['companyName'], item['name'], item['salesStatus']), (self.product_id, self.company_id, self.name, self.name, 'on_sale'))
        self.assertEqual(item['integrationStatus'], 'not_started')
        self.assertTrue(item['statusFetchedAt'].endswith('Z'))

    def test_filters_use_confirmed_values_and_pagination(self):
        self.cursor.execute("UPDATE product SET confirmed_name = %s, confirmed_sales_status = 'off_sale', integration_status = 'completed' WHERE id = %s", ('确认产品', self.product_id))
        headers = {'Authorization': 'Bearer token'}
        result = self.client.get('/api/products', params={'companyId': self.company_id, 'q': '确认产', 'status': 'off_sale', 'integrationStatus': 'partial_failed', 'limit': 1, 'offset': 0}, headers=headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual([item['id'] for item in result.json()['items']], [self.product_id])
        self.assertEqual(result.json()['items'][0]['integrationStatus'], 'partial_failed')
        no_match = self.client.get('/api/products', params={'companyId': self.company_id, 'status': 'on_sale'}, headers=headers)
        self.assertEqual(no_match.json()['items'], [])
        next_page = self.client.get('/api/products', params={'companyId': self.company_id, 'limit': 1, 'offset': 1}, headers=headers)
        self.assertEqual(next_page.json()['items'], [])

    def test_material_status_filter_reads_crawl_database(self):
        self.crawl_cursor.execute("INSERT INTO product_document_status (product_id, type, status) VALUES (%s, 'clause', 'not_found')", (self.product_id,))
        headers = {'Authorization': 'Bearer token'}
        matched = self.client.get('/api/products', params={'companyId': self.company_id, 'materialType': 'clause', 'materialStatus': 'not_found'}, headers=headers)
        self.assertEqual([item['id'] for item in matched.json()['items']], [self.product_id])
        unmatched = self.client.get('/api/products', params={'companyId': self.company_id, 'materialType': 'clause', 'materialStatus': 'not_run'}, headers=headers)
        self.assertEqual(unmatched.json()['items'], [])
        other = self.client.get('/api/products', params={'companyId': self.company_id, 'materialType': 'rate_table', 'materialStatus': 'not_run'}, headers=headers)
        self.assertEqual([item['id'] for item in other.json()['items']], [self.product_id])

    def test_current_document_takes_precedence_over_failed_status(self):
        self.crawl_cursor.execute("INSERT INTO product_document_status (product_id, type, status) VALUES (%s, 'clause', 'download_failed')", (self.product_id,))
        self.cursor.execute("INSERT INTO document (product_id, type, link_status, sha256, mime_type, size, storage_key) VALUES (%s, 'clause', 'current', %s, 'application/pdf', 8, %s)", (self.product_id, 'a' * 64, self.name))
        headers = {'Authorization': 'Bearer token'}
        for status, expected in [('downloaded', True), ('download_failed', False), ('not_run', False)]:
            with self.subTest(status=status):
                response = self.client.get('/api/products', params={'companyId': self.company_id, 'materialType': 'clause', 'materialStatus': status}, headers=headers)
                self.assertEqual(bool(response.json()['items']), expected)
        missing_any = self.client.get('/api/products', params={'companyId': self.company_id, 'materialStatus': 'not_run'}, headers=headers)
        self.assertEqual([row['id'] for row in missing_any.json()['items']], [self.product_id])

    def test_product_detail_combines_materials_and_missing_returns_404(self):
        self.crawl_cursor.execute("INSERT INTO product_document_status (product_id, type, status) VALUES (%s, 'rate_table', 'not_found')", (self.product_id,))
        headers = {'Authorization': 'Bearer token'}
        response = self.client.get(f'/api/products/{self.product_id}', headers=headers)
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail['id'], self.product_id)
        self.assertEqual(detail['materials'], [
            {'type': 'clause', 'label': '条款', 'status': 'not_run'},
            {'type': 'rate_table', 'label': '费率表', 'status': 'not_found'},
            {'type': 'cash_value_table', 'label': '现金价值表', 'status': 'not_run'},
            {'type': 'prospectus', 'label': '说明书', 'status': 'not_run'},
        ])
        self.assertEqual(detail['sources'], [])
        self.assertEqual(detail['history'], [])
        missing = self.client.get('/api/products/999999999999', headers=headers)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()['code'], 'not_found')

    def test_detail_exposes_current_and_historical_sources_with_snapshot(self):
        self.cursor.execute("INSERT INTO document (product_id, type, link_status, sha256, mime_type, size, storage_key, source_url, download_url) VALUES (%s, 'clause', 'current', %s, 'application/pdf', 8, %s, %s, %s)", (self.product_id, 'b' * 64, self.name, 'https://example.com/product', 'https://example.com/current.pdf'))
        current_id = self.cursor.lastrowid
        self.cursor.execute("INSERT INTO document (product_id, type, link_status, sha256, mime_type, size, storage_key) VALUES (%s, 'clause', 'historical', %s, 'application/pdf', 10, %s)", (self.product_id, 'c' * 64, self.name))
        old_id = self.cursor.lastrowid
        self.crawl_cursor.execute("INSERT INTO document_snapshot (document_id, product_ids, source_url, download_url, snapshot_key, fetched_at) VALUES (%s,%s,%s,%s,%s,UTC_TIMESTAMP())", (current_id, json.dumps([self.product_id]), 'https://example.com/page', 'https://example.com/current.pdf', 'snapshot-key'))
        snapshot_id = self.crawl_cursor.lastrowid
        response = self.client.get(f'/api/products/{self.product_id}', headers={'Authorization': 'Bearer token'})
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail['materials'][0]['status'], 'downloaded')
        self.assertEqual((detail['sources'][0]['id'], detail['sources'][0]['documentId'], detail['sources'][0]['snapshotKey']), (snapshot_id, current_id, 'snapshot-key'))
        self.assertEqual(detail['sources'][0]['sourceUrl'], 'https://example.com/product')
        self.assertEqual(detail['history'][0]['documentId'], old_id)
        self.assertEqual(detail['history'][0]['id'], 0)

    def test_correction_preserves_collected_values_and_filters_confirmed_values(self):
        headers = {'Authorization': 'Bearer token'}
        response = self.client.patch(f'/api/products/{self.product_id}', headers=headers, json={'name': ' 确认产品 ', 'salesStatus': 'off_sale', 'correctionNote': ' 核对 '})
        self.assertEqual(response.status_code, 200)
        item = response.json()
        self.assertEqual(item['name'], self.name)
        self.assertEqual(item['salesStatus'], 'on_sale')
        self.assertEqual((item['confirmedName'], item['confirmedSalesStatus'], item['correctionNote'], item['confirmedBy']), ('确认产品', 'off_sale', '核对', 'internal-token'))
        self.assertTrue(item['confirmedAt'].endswith('Z'))
        result = self.client.get('/api/products', params={'companyId': self.company_id, 'q': '确认产品', 'status': 'off_sale'}, headers=headers)
        self.assertEqual([row['id'] for row in result.json()['items']], [self.product_id])
        missing = self.client.patch('/api/products/999999999999', headers=headers, json={'name': '不存在'})
        self.assertEqual(missing.status_code, 404)

    def test_correction_rejects_invalid_or_empty_input(self):
        headers = {'Authorization': 'Bearer token'}
        for payload in ({}, {'name': '  '}, {'salesStatus': 'invalid'}):
            response = self.client.patch(f'/api/products/{self.product_id}', headers=headers, json=payload)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['code'], 'validation_error')
