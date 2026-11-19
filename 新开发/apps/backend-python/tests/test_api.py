import unittest

from fastapi.testclient import TestClient

from baoxian_api.app import create_app


class ApiTests(unittest.TestCase):
    def test_health_is_public(self):
        with TestClient(create_app(internal_api_token="test-token")) as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["service"], "baoxian-api")

    def test_every_other_path_requires_token_even_if_unknown(self):
        with TestClient(create_app(internal_api_token="test-token")) as client:
            for path in ("/api/system", "/not-a-route"):
                for headers in ({}, {"Authorization": "Bearer wrong"}):
                    with self.subTest(path=path, headers=headers):
                        response = client.get(path, headers=headers)
                        self.assertEqual(response.status_code, 401)
                        self.assertEqual(response.json()["code"], "unauthorized")

    def test_system_is_accessible_with_valid_token(self):
        with TestClient(create_app(internal_api_token="test-token")) as client:
            response = client.get("/api/system", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "baoxian-api")
        self.assertEqual(response.json()["version"], "0.1.0")

    def test_application_configuration_requires_token_and_database(self):
        from baoxian_api.main import build_from_env

        for env in ({}, {"INTERNAL_API_TOKEN": "token"}, {"DATABASE_URL": "mysql://example/db"}, {"INTERNAL_API_TOKEN": "token", "DATABASE_URL": "mysql://example/db"}, {"INTERNAL_API_TOKEN": "token", "DATABASE_URL": "mysql://example/db", "CRAWL_DATABASE_URL": "mysql://example/crawl"}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                build_from_env(env)

    def test_categories_return_association_codes(self):
        with TestClient(create_app(internal_api_token="test-token")) as client:
            response = client.get("/api/categories", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"source": "association", "items": [
            {"code": "PubNewProdTypeCode_00", "name": "人寿保险"},
            {"code": "PubNewProdTypeCode_01", "name": "年金保险"},
            {"code": "PubNewProdTypeCode_02", "name": "健康保险"},
            {"code": "PubNewProdTypeCode_03", "name": "意外伤害保险"},
            {"code": "PubNewProdTypeCode_04", "name": "委托管理业务"},
        ]})

    def test_index_overview_combines_progress_tasks_and_counts(self):
        class Tasks:
            def list(self, status=None, task_type=None):
                return [{'id': 9, 'status': 'running', 'scope': {'category': {'code': 'PubNewProdTypeCode_00'}}, 'reportedTotal': None, 'finishedAt': None}, {'id': 8, 'status': 'completed', 'scope': {'category': {'code': 'PubNewProdTypeCode_00'}}, 'reportedTotal': 12, 'finishedAt': '2026-01-01T00:00:00.000Z'}]
            def index_progress(self):
                return [{'categoryCode': 'PubNewProdTypeCode_00', 'nextPage': 3, 'cycle': 2, 'completedAt': None, 'updatedAt': '2026-01-02T00:00:00.000Z'}]
        class Products:
            def count_by_category(self): return {'PubNewProdTypeCode_00': 7}
        with TestClient(create_app(internal_api_token='token', task_service=Tasks(), product_service=Products())) as client:
            response = client.get('/api/index-overview', headers={'Authorization': 'Bearer token'})
        self.assertEqual(response.status_code, 200)
        items = response.json()['items']
        self.assertEqual(len(items), 5)
        self.assertEqual({key: items[0][key] for key in ('status', 'productCount', 'cycle', 'nextPage', 'completedPages', 'reportedTotal', 'latestTask', 'latestCompletedAt')}, {'status': 'running', 'productCount': 7, 'cycle': 2, 'nextPage': 3, 'completedPages': 2, 'reportedTotal': 12, 'latestTask': {'id': 9, 'status': 'running'}, 'latestCompletedAt': '2026-01-01T00:00:00.000Z'})
        self.assertEqual(items[1]['status'], 'not_started')

    def test_manual_refresh_requires_auth_and_returns_created_task_ids(self):
        class Refresh:
            calls = 0
            async def check(self):
                self.calls += 1
                return {'created': [12, 34]}
        refresh = Refresh()
        with TestClient(create_app(internal_api_token='token', refresh_scheduler=refresh)) as client:
            self.assertEqual(client.post('/api/index-refresh/check').status_code, 401)
            self.assertEqual(refresh.calls, 0)
            response = client.post('/api/index-refresh/check', headers={'Authorization': 'Bearer token'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'created': [12, 34]})
        self.assertEqual(refresh.calls, 1)

    def test_development_application_wires_manual_refresh_route(self):
        from baoxian_api.main import build_from_env
        app = build_from_env({'INTERNAL_API_TOKEN': 'token', 'DATABASE_URL': 'mysql://localhost/product', 'CRAWL_DATABASE_URL': 'mysql://localhost/crawl', 'REDIS_URL': 'redis://localhost'})
        self.assertIn(('/api/index-refresh/check', 'POST'), {(route.path, method) for route in app.routes for method in getattr(route, 'methods', set())})

    def test_running_factory_matches_documented_http_contract(self):
        import re
        from pathlib import Path
        from baoxian_api.main import build_from_env
        contract = Path(__file__).resolve().parents[3] / 'docs' / '0.项目整体' / '后端Python接口与测试行为映射.md'
        route_pattern = re.compile(r"^\| `(GET|POST|PUT|PATCH|DELETE) (/[^ `]+)` \|", re.MULTILINE)
        normalize = lambda path: re.sub(r'\{[^}]+\}|:[A-Za-z_][A-Za-z_0-9]*', '{id}', path)
        expected = {(method, normalize(path)) for method, path in route_pattern.findall(contract.read_text(encoding='utf-8'))}
        app = build_from_env({'INTERNAL_API_TOKEN': 'token', 'DATABASE_URL': 'mysql://localhost/product', 'CRAWL_DATABASE_URL': 'mysql://localhost/crawl', 'REDIS_URL': 'redis://localhost'})
        actual = {(method.upper(), normalize(path)) for path, operations in app.openapi()['paths'].items()
                  for method in operations}
        self.assertIn(('GET', '/health'), expected)
        self.assertIn(('GET', '/api/documents/{id}/parsed-content'), expected)
        self.assertEqual(actual, expected)
