import unittest

from fastapi.testclient import TestClient

from test_product_api import test_crawl_url
from baoxian_api.app import create_app
from baoxian_api.settings import MySQLSettingsService


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.service = MySQLSettingsService(test_crawl_url())
        self.connection = self.service._connect()
        self.cursor = self.connection.cursor()
        self.cursor.execute('SELECT * FROM app_setting WHERE id = 1')
        self.original = self.cursor.fetchone()
        self.client = TestClient(create_app(internal_api_token='token', settings_service=self.service))
        self.headers = {'Authorization': 'Bearer token'}

    def tearDown(self):
        self.client.close()
        self.cursor.execute('DELETE FROM app_setting WHERE id = 1')
        if self.original:
            keys = list(self.original)
            self.cursor.execute('INSERT INTO app_setting (' + ','.join(keys) + ') VALUES (' + ','.join(['%s'] * len(keys)) + ')', [self.original[key] for key in keys])
        self.cursor.close()
        self.connection.close()

    def test_defaults_and_partial_update_persist(self):
        self.cursor.execute('DELETE FROM app_setting WHERE id = 1')
        initial = self.client.get('/api/settings/runtime', headers=self.headers)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json(), {'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000, 'retryAttempts': 3, 'indexRefreshMinutes': 10080, 'associationCrawlEnabled': True})
        updated = self.client.put('/api/settings/runtime', headers=self.headers, json={'indexRefreshMinutes': 2880, 'associationCrawlEnabled': False})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()['indexRefreshMinutes'], 2880)
        self.assertFalse(updated.json()['associationCrawlEnabled'])
        self.assertEqual(updated.json()['globalConcurrency'], 2)
        self.assertEqual(self.client.get('/api/settings/runtime', headers=self.headers).json(), updated.json())

    def test_invalid_settings_are_rejected_without_persisting(self):
        before = self.client.get('/api/settings/runtime', headers=self.headers).json()
        for payload in ({}, {'globalConcurrency': 0}, {'domainIntervalMs': 99}, {'requestTimeoutMs': 120001}, {'retryAttempts': -1}, {'indexRefreshMinutes': 0}, {'associationCrawlEnabled': 'false'}, {'globalConcurrency': True}):
            with self.subTest(payload=payload):
                response = self.client.put('/api/settings/runtime', headers=self.headers, json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['code'], 'validation_error')
        self.assertEqual(self.client.get('/api/settings/runtime', headers=self.headers).json(), before)
