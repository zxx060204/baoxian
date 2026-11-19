import unittest

from fastapi.testclient import TestClient

from baoxian_api.app import create_app


class TaskInputContractTests(unittest.TestCase):
    def test_null_optional_scope_fields_are_rejected_before_task_creation(self):
        class Tasks:
            def __init__(self):
                self.created = []

            async def create_index_task(self, *args):
                self.created.append(args)
                return {'id': 1}

            async def create_company_task(self, *args):
                self.created.append(args)
                return {'id': 2}

            async def create_company_site_discovery_task(self, *args):
                self.created.append(args)
                return {'id': 3}

        class Companies:
            def list(self):
                return [{'id': 1, 'name': '测试公司', 'officialSiteUrl': None},
                        {'id': 2, 'name': '有官网公司', 'officialSiteUrl': 'https://example.test'}]

        class Products:
            def list(self, **kwargs):
                return [{'id': 4, 'name': '测试产品'}]

        tasks = Tasks()
        payloads = [
            {'type': 'association_index', 'category': 'PubNewProdTypeCode_00', 'maxPages': None},
            {'type': 'association_index', 'category': 'PubNewProdTypeCode_00', 'saleAfter': None},
            {'type': 'company_documents', 'companyId': 2, 'productIds': None},
            {'type': 'company_site_discovery', 'companyIds': None},
        ]
        with TestClient(create_app(internal_api_token='token', task_service=tasks,
                                   company_service=Companies(), product_service=Products())) as client:
            for payload in payloads:
                with self.subTest(payload=payload):
                    response = client.post('/api/crawl-tasks', headers={'Authorization': 'Bearer token'}, json=payload)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()['code'], 'validation_error')
        self.assertEqual(tasks.created, [])

    def test_omitted_optional_scope_fields_still_create_tasks(self):
        class Tasks:
            def __init__(self): self.created = []
            async def create_index_task(self, *args):
                self.created.append(('index', args))
                return {'id': 1}
            async def create_company_task(self, *args):
                self.created.append(('documents', args))
                return {'id': 2}
            async def create_company_site_discovery_task(self, *args):
                self.created.append(('discovery', args))
                return {'id': 3}

        class Companies:
            def list(self):
                return [{'id': 1, 'name': '测试公司', 'officialSiteUrl': None},
                        {'id': 2, 'name': '有官网公司', 'officialSiteUrl': 'https://example.test'}]

        class Products:
            def list(self, **kwargs): return [{'id': 4, 'name': '测试产品'}]

        tasks = Tasks()
        payloads = [
            {'type': 'association_index', 'category': 'PubNewProdTypeCode_00'},
            {'type': 'company_documents', 'companyId': 2},
            {'type': 'company_site_discovery'},
        ]
        with TestClient(create_app(internal_api_token='token', task_service=tasks,
                                   company_service=Companies(), product_service=Products())) as client:
            for payload in payloads:
                with self.subTest(payload=payload):
                    response = client.post('/api/crawl-tasks', headers={'Authorization': 'Bearer token'}, json=payload)
                    self.assertEqual(response.status_code, 202)
        self.assertEqual([item[0] for item in tasks.created], ['index', 'documents', 'discovery'])


if __name__ == '__main__':
    unittest.main()
