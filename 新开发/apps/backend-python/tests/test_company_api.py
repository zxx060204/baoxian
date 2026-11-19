import os
import unittest
from pathlib import Path
from urllib.parse import urlparse, unquote
from uuid import uuid4

import pymysql
from fastapi.testclient import TestClient

from baoxian_api.app import create_app
from baoxian_api.companies import MySQLCompanyService


def test_database_url():
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        env_file = Path(__file__).resolve().parents[3] / ".env"
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, item = line.partition("=")
            if sep and key.strip() == "TEST_DATABASE_URL":
                value = item.strip().strip("\"'")
                break
    if not value or urlparse(value).path != "/baoxian_product_test":
        raise RuntimeError("公司集成测试只允许使用 baoxian_product_test")
    return value


class CompanyApiTests(unittest.TestCase):
    def setUp(self):
        self.url = test_database_url()
        self.name = "python-tdd-" + uuid4().hex
        self.service = MySQLCompanyService(self.url)
        self.client = TestClient(create_app(internal_api_token="test-token", company_service=self.service))
        self.headers = {"Authorization": "Bearer test-token"}

    def tearDown(self):
        self.client.close()
        parsed = urlparse(self.url)
        with pymysql.connect(host=parsed.hostname, port=parsed.port or 3306, user=unquote(parsed.username or ""), password=unquote(parsed.password or ""), database=parsed.path[1:], charset="utf8mb4", autocommit=True) as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM company WHERE name IN (%s, %s, %s, %s)", (self.name, self.name + "-other", self.name.upper() + " INSURANCE", self.name + " insurance"))

    def test_create_company_then_list_it(self):
        response = self.client.post("/api/companies", headers=self.headers, json={"name": self.name, "officialSiteUrl": "https://example.com/path?q=1"})
        self.assertEqual(response.status_code, 201)
        company = response.json()
        self.assertEqual(company["name"], self.name)
        self.assertEqual(company["officialSiteUrl"], "https://example.com")
        self.assertIsInstance(company["id"], int)
        listed = self.client.get("/api/companies", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertTrue(any(row["id"] == company["id"] for row in listed.json()["items"]))

    def test_whitespace_is_normalized_without_folding_case(self):
        upper = self.name.upper() + ' INSURANCE'
        lower = self.name + ' insurance'
        created = self.client.post('/api/companies', headers=self.headers, json={'name': '  ' + self.name.upper() + '  \n  INSURANCE  '})
        self.assertEqual((created.status_code, created.json()['name']), (201, upper))
        duplicate = self.client.post('/api/companies', headers=self.headers, json={'name': upper})
        self.assertEqual(duplicate.status_code, 409)
        different = self.client.post('/api/companies', headers=self.headers, json={'name': lower})
        self.assertEqual((different.status_code, different.json()['name']), (201, lower))
        updated = self.client.patch(f"/api/companies/{different.json()['id']}", headers=self.headers, json={'name': '  ' + self.name + '  '})
        self.assertEqual((updated.status_code, updated.json()['name']), (200, self.name))
        self.assertNotIn('integrationStatus', created.json())

    def test_document_entry_and_notes_are_returned_by_list(self):
        payload = {'name': self.name, 'officialSiteUrl': 'https://example.com/path', 'documentEntryUrl': 'https://example.com/list?page=1', 'notes': '示例公司'}
        response = self.client.post('/api/companies', headers=self.headers, json=payload)
        self.assertEqual(response.status_code, 201)
        company = response.json()
        item = next(row for row in self.client.get('/api/companies', headers=self.headers).json()['items'] if row['id'] == company['id'])
        self.assertEqual((item['officialSiteUrl'], item['documentEntryUrl'], item['notes']), ('https://example.com', payload['documentEntryUrl'], payload['notes']))
        self.assertNotIn('normalizedName', company)

    def test_duplicate_company_is_rejected_after_whitespace_cleanup(self):
        first = self.client.post("/api/companies", headers=self.headers, json={"name": self.name})
        self.assertEqual(first.status_code, 201)
        duplicate = self.client.post("/api/companies", headers=self.headers, json={"name": "  " + self.name + "  "})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["code"], "duplicate_company")
        matches = [item for item in self.client.get('/api/companies', headers=self.headers).json()['items'] if item['name'] == self.name]
        self.assertEqual(len(matches), 1)

    def test_invalid_company_inputs_are_rejected(self):
        for payload in ({"name": "   "}, {"name": self.name, "officialSiteUrl": "ftp://example.com"}, {"name": self.name, "notes": "x" * 2001}, {}):
            with self.subTest(payload=payload):
                response = self.client.post("/api/companies", headers=self.headers, json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "validation_error")

    def test_blank_name_validation_does_not_insert_empty_company(self):
        with self.service._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS n FROM company WHERE name=''")
                before = cursor.fetchone()['n']
                response = self.client.post('/api/companies', headers=self.headers, json={'name': '   '})
                cursor.execute("SELECT COUNT(*) AS n FROM company WHERE name=''")
                self.assertEqual(cursor.fetchone()['n'], before)
        self.assertEqual((response.status_code, response.json()['code']), (400, 'validation_error'))

    def test_update_preserves_document_entry_when_omitted(self):
        created = self.client.post("/api/companies", headers=self.headers, json={"name": self.name, "documentEntryUrl": "https://example.com/list?q=1"}).json()
        response = self.client.patch(f"/api/companies/{created['id']}", headers=self.headers, json={"name": "  " + self.name + "  ", "officialSiteUrl": "https://example.org/page", "notes": " updated "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], self.name)
        self.assertEqual(response.json()["documentEntryUrl"], "https://example.com/list?q=1")
        self.assertEqual(response.json()["officialSiteUrl"], "https://example.org")
        self.assertEqual(response.json()["notes"], "updated")
        item = next(row for row in self.client.get('/api/companies', headers=self.headers).json()['items'] if row['id'] == created['id'])
        self.assertEqual((item['name'], item['officialSiteUrl'], item['documentEntryUrl'], item['notes']), (self.name, 'https://example.org', 'https://example.com/list?q=1', 'updated'))

    def test_update_missing_company_returns_not_found(self):
        response = self.client.patch("/api/companies/999999999999", headers=self.headers, json={"name": self.name})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")

    def test_update_to_existing_name_keeps_original_company(self):
        self.client.post("/api/companies", headers=self.headers, json={"name": self.name})
        other = self.client.post("/api/companies", headers=self.headers, json={"name": self.name + "-other"}).json()
        response = self.client.patch(f"/api/companies/{other['id']}", headers=self.headers, json={"name": self.name})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "duplicate_company")
        items = self.client.get("/api/companies", headers=self.headers).json()["items"]
        self.assertEqual(next(row for row in items if row["id"] == other["id"])["name"], self.name + "-other")
