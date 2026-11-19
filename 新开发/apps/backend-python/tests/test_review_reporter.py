import unittest
from uuid import uuid4

from test_product_api import test_crawl_url
from test_company_api import test_database_url
from baoxian_api.companies import MySQLCompanyService
from baoxian_api.review_reporter import MySQLReviewReporter
from baoxian_api.app import create_app
from fastapi.testclient import TestClient


class ReviewReporterTests(unittest.TestCase):
    def setUp(self):
        self.service = MySQLReviewReporter(test_crawl_url(), test_database_url())
        self.task_id = int(uuid4().int % 1000000000000) + 1000000000000

    def tearDown(self):
        with self.service._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM review_item WHERE task_id = %s', (self.task_id,))

    def test_difference_items_are_deduplicated_and_identity_is_preserved(self):
        first = self.service.record_correction_diff(self.task_id, self.task_id, 'name', '确认名', '采集名')
        repeat = self.service.record_correction_diff(self.task_id, self.task_id, 'name', '确认名', '采集名')
        source = self.service.record_source_value_change(self.task_id, self.task_id, '在售', '停用')
        identity = self.service.record_product_identity(self.task_id, {'name': '采集名'}, [{'id': self.task_id}])
        self.assertEqual(first['id'], repeat['id'])
        self.assertEqual(first['type'], 'correction_diff')
        self.assertEqual(first['payload'], {'kind': 'correction_diff', 'productId': self.task_id, 'field': 'name', 'confirmedValue': '确认名', 'collectedValue': '采集名'})
        self.assertEqual(source['type'], 'source_value_diff')
        self.assertEqual(source['payload']['confirmedValue'], '在售')
        self.assertEqual(identity['payload'], {'kind': 'product_identity', 'incoming': {'name': '采集名'}, 'candidates': [{'id': self.task_id}]})

    def test_review_list_filters_and_returns_payload(self):
        first = self.service.record_correction_diff(self.task_id, self.task_id, 'name', '确认名', '采集名')
        second = self.service.record_product_identity(self.task_id, {'name': '产品'}, [{'id': self.task_id}])
        with TestClient(create_app(internal_api_token='token', review_service=self.service)) as client:
            headers = {'Authorization': 'Bearer token'}
            self.assertEqual(client.get('/api/review-items').status_code, 401)
            response = client.get('/api/review-items', headers=headers, params={'status': 'pending', 'type': 'product_identity'})
            self.assertEqual(response.status_code, 200)
            items = response.json()['items']
            own = [item for item in items if item['taskId'] == self.task_id]
            self.assertEqual([item['id'] for item in own], [second['id']])
            self.assertEqual(own[0]['payload']['incoming']['name'], '产品')
            self.assertEqual(client.get('/api/review-items', headers=headers, params={'status': 'invalid'}).status_code, 400)

    def test_company_site_resolution_validates_candidate_and_updates_company(self):
        product = MySQLCompanyService(test_database_url())
        name = 'python-review-' + uuid4().hex
        with product._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute('INSERT INTO company (name) VALUES (%s)', (name,))
                company_id = cursor.lastrowid
        item = self.service.record_company_site(self.task_id, company_id, name, [{'url': 'https://example.test/path', 'score': 1}])
        try:
            with TestClient(create_app(internal_api_token='token', review_service=self.service)) as client:
                headers = {'Authorization': 'Bearer token'}
                url = f"/api/review-items/{item['id']}/resolve"
                invalid = client.post(url, headers=headers, json={'action': 'select_site', 'siteUrl': 'https://other.test'})
                self.assertEqual((invalid.status_code, invalid.json()['code']), (400, 'invalid_resolution'))
                from unittest.mock import patch
                from baoxian_api.tasks import public_row
                def fail(row):
                    if 'resolution' in row and row['resolution'] == 'select_site': raise RuntimeError('故障注入')
                    return public_row(row)
                with patch('baoxian_api.review_reporter.public_row', side_effect=fail):
                    with TestClient(create_app(internal_api_token='token', review_service=self.service), raise_server_exceptions=False) as failing_client:
                        failed = failing_client.post(url, headers=headers, json={'action': 'select_site', 'siteUrl': 'https://example.test/path'})
                        self.assertEqual(failed.status_code, 500)
                with product._connect() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute('SELECT official_site_url FROM company WHERE id=%s', (company_id,))
                        self.assertIsNone(cursor.fetchone()['official_site_url'])
                chosen = client.post(url, headers=headers, json={'action': 'select_site', 'siteUrl': 'https://example.test/path'})
                self.assertEqual((chosen.status_code, chosen.json()['item']['status']), (200, 'resolved'))
                self.assertEqual(client.post(url, headers=headers, json={'action': 'ignore'}).status_code, 409)
            with product._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute('SELECT official_site_url FROM company WHERE id=%s', (company_id,))
                    self.assertEqual(cursor.fetchone()['official_site_url'], 'https://example.test')
        finally:
            with product._connect() as conn:
                with conn.cursor() as cursor: cursor.execute('DELETE FROM company WHERE id=%s AND name=%s', (company_id, name))

    def test_ignore_company_site_marks_dismissed(self):
        item = self.service.record_company_site(self.task_id, self.task_id, '测试公司', [])
        with TestClient(create_app(internal_api_token='token', review_service=self.service)) as client:
            response = client.post(f"/api/review-items/{item['id']}/resolve", headers={'Authorization': 'Bearer token'}, json={'action': 'ignore'})
        self.assertEqual((response.status_code, response.json()['item']['status']), (200, 'dismissed'))
