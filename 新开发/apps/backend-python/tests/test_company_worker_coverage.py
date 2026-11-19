import json
import unittest
from uuid import uuid4

from baoxian_api.companies import MySQLCompanyService
from baoxian_api.company_worker import CompanyDocumentProcessor
from baoxian_api.document_ingestion import DocumentIngestionService
from baoxian_api.products import MySQLProductService
from baoxian_api.review_reporter import MySQLReviewReporter
from baoxian_api.tasks import MySQLTaskService
from test_company_api import test_database_url
from test_product_api import test_crawl_url


class MemoryStorage:
    async def put(self, key, body, content_type):
        return key


class CompanyWorkerCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.product_connection = MySQLCompanyService(test_database_url())._connect()
        self.product_cursor = self.product_connection.cursor()
        self.crawl_connection = MySQLCompanyService(test_crawl_url())._connect()
        self.crawl_cursor = self.crawl_connection.cursor()
        self.marker = 'company-coverage-' + uuid4().hex
        self.product_cursor.execute('INSERT INTO company (name,official_site_url) VALUES (%s,%s)', (self.marker, 'https://www.aia.com.cn'))
        self.company_id = self.product_cursor.lastrowid
        self.product_cursor.execute("INSERT INTO product (company_id,name,normalized_name,identity_group,sales_status,status_fetched_at,source_url,category_code,category_name) VALUES (%s,%s,%s,'individual','on_sale',UTC_TIMESTAMP(),'https://www.aia.com.cn','test','测试')", (self.company_id, self.marker, self.marker))
        self.on_sale_id = self.product_cursor.lastrowid
        self.stopped_name = self.marker + '-停售组'
        self.product_cursor.execute("INSERT INTO product (company_id,name,normalized_name,identity_group,sales_status,sales_status_raw,status_fetched_at,source_url,category_code,category_name) VALUES (%s,%s,%s,'stopped','stopped','停售/停用',UTC_TIMESTAMP(),'https://www.aia.com.cn','test','测试')", (self.company_id, self.stopped_name, self.stopped_name))
        self.stopped_id = self.product_cursor.lastrowid
        self.scope = {
            'category': {'code': f'company:{self.company_id}', 'name': self.marker},
            'company': {'id': self.company_id, 'name': self.marker, 'officialSiteUrl': 'https://www.aia.com.cn'},
            'products': [
                {'id': self.on_sale_id, 'name': self.marker, 'salesStatus': 'on_sale'},
                {'id': self.stopped_id, 'name': self.stopped_name, 'salesStatus': 'stopped'},
            ],
        }
        self.crawl_cursor.execute("INSERT INTO crawl_task (type,status,scope,config) VALUES ('company_documents','queued',%s,%s)", (json.dumps(self.scope, ensure_ascii=False), json.dumps({'retryAttempts': 1})))
        self.task_id = self.crawl_cursor.lastrowid
        self.crawl_cursor.executemany("INSERT INTO crawl_task_unit (task_id,unit_key,page,state) VALUES (%s,%s,1,'pending')", [
            (self.task_id, f'company:{self.company_id}:product:{self.on_sale_id}'),
            (self.task_id, f'company:{self.company_id}:product:{self.stopped_id}'),
        ])
        async def download(url, **kwargs):
            return {'ok': True, 'body': b'%PDF-example', 'contentType': 'application/pdf', 'finalUrl': url}
        self.ingestion = DocumentIngestionService(test_database_url(), test_crawl_url(), MemoryStorage(), download)
        self.review = MySQLReviewReporter(test_crawl_url())
        self.tasks = MySQLTaskService(test_crawl_url())

    async def asyncTearDown(self):
        self.crawl_cursor.execute('DELETE FROM review_item WHERE task_id=%s', (self.task_id,))
        self.crawl_cursor.execute('DELETE FROM document_snapshot WHERE task_id=%s', (self.task_id,))
        self.crawl_cursor.execute('DELETE FROM document_failure WHERE task_id=%s', (self.task_id,))
        self.crawl_cursor.execute('DELETE FROM product_document_status WHERE product_id IN (%s,%s)', (self.on_sale_id, self.stopped_id))
        self.crawl_cursor.execute('DELETE FROM crawl_task_unit WHERE task_id=%s', (self.task_id,))
        self.crawl_cursor.execute('DELETE FROM crawl_task WHERE id=%s', (self.task_id,))
        self.product_cursor.execute('DELETE FROM document WHERE product_id IN (%s,%s) OR (product_id IS NULL AND download_url LIKE %s)', (self.on_sale_id, self.stopped_id, '%' + self.marker + '%'))
        self.product_cursor.execute('DELETE FROM product WHERE id IN (%s,%s)', (self.on_sale_id, self.stopped_id))
        self.product_cursor.execute('DELETE FROM company WHERE id=%s', (self.company_id,))
        self.crawl_cursor.close()
        self.crawl_connection.close()
        self.product_cursor.close()
        self.product_connection.close()

    async def test_unsearched_sales_status_is_not_marked_failed_or_not_found(self):
        class Adapter:
            async def discover_documents(self, company, products):
                return {
                    'candidates': [],
                    'searchedPages': ['https://cws.aia.com.cn/mypage/productpublish/list'],
                    'searchedProductIds': [products[0]['id']],
                    'notFoundProductIds': [],
                    'unsearchedProductIds': [products[1]['id']],
                    'pageFailures': [{
                        'url': 'https://cws.aia.com.cn/mypage/productpublish/list',
                        'message': '友邦在售目录读取失败',
                        'productIds': [products[0]['id']],
                    }],
                }

        await CompanyDocumentProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)

        task = self.tasks.get(self.task_id)
        self.assertEqual(task['status'], 'partial_failed')
        self.assertEqual(task['scope']['unsearchedProducts'], [{'id': self.stopped_id, 'name': self.stopped_name, 'reason': '本轮未搜索该产品销售状态'}])
        units = {unit['unitKey']: unit['state'] for unit in task['units']}
        self.assertEqual(units[f'company:{self.company_id}:product:{self.on_sale_id}'], 'failed')
        self.assertEqual(units[f'company:{self.company_id}:product:{self.stopped_id}'], 'completed')
        detail = MySQLProductService(test_database_url(), test_crawl_url()).get(self.stopped_id)
        self.assertEqual(detail['integrationStatus'], 'not_started')
        self.assertTrue(all(item['status'] == 'not_run' for item in detail['materials']))
        self.crawl_cursor.execute('SELECT COUNT(*) AS n FROM product_document_status WHERE product_id=%s', (self.stopped_id,))
        self.assertEqual(self.crawl_cursor.fetchone()['n'], 0)

    async def test_directory_failure_only_fails_products_in_that_status_group(self):
        class Adapter:
            async def discover_documents(self, company, products):
                return {
                    'candidates': [],
                    'searchedPages': ['https://cws.aia.com.cn/mypage/productpublish/list'],
                    'searchedSalesStatuses': ['在售'],
                    'searchedProductIds': [products[0]['id']],
                    'notFoundProductIds': [products[0]['id']],
                    'unsearchedProductIds': [],
                    'pageFailures': [{
                        'url': 'https://cws.aia.com.cn/mypage/productpublish/list',
                        'message': '友邦停售目录读取失败',
                        'productIds': [products[1]['id']],
                    }],
                }

        await CompanyDocumentProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)

        task = self.tasks.get(self.task_id)
        self.assertEqual(task['status'], 'partial_failed')
        units = {unit['unitKey']: unit['state'] for unit in task['units']}
        self.assertEqual(units[f'company:{self.company_id}:product:{self.on_sale_id}'], 'completed')
        self.assertEqual(units[f'company:{self.company_id}:product:{self.stopped_id}'], 'failed')
        active = MySQLProductService(test_database_url(), test_crawl_url()).get(self.on_sale_id)
        stopped = MySQLProductService(test_database_url(), test_crawl_url()).get(self.stopped_id)
        self.assertEqual(active['integrationStatus'], 'not_found')
        self.assertEqual(stopped['integrationStatus'], 'partial_failed')

    async def test_ambiguous_document_is_kept_unassigned_and_added_to_review(self):
        candidate = {
            'productIds': [self.on_sale_id, self.stopped_id],
            'type': 'clause', 'title': self.marker + ' 条款',
            'downloadUrl': 'https://example.test/' + self.marker + '.pdf',
            'sourcePageUrl': 'https://example.test/list', 'snapshotHtml': '<html>同名候选</html>',
        }
        class Adapter:
            async def discover_documents(self, company, products):
                return {'candidates': [candidate], 'searchedPages': [candidate['sourcePageUrl']], 'pageFailures': []}

        await CompanyDocumentProcessor(test_crawl_url(), Adapter(), self.ingestion, self.review).process(self.task_id)

        self.crawl_cursor.execute("SELECT payload FROM review_item WHERE task_id=%s AND type='document_attribution'", (self.task_id,))
        review = self.crawl_cursor.fetchone()
        self.assertIsNotNone(review)
        payload = json.loads(review['payload'])
        self.assertEqual(payload['candidate']['productIds'], [self.on_sale_id, self.stopped_id])
        self.product_cursor.execute('SELECT product_id FROM document WHERE download_url=%s', (candidate['downloadUrl'],))
        self.assertIsNone(self.product_cursor.fetchone()['product_id'])
