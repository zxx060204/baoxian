from datetime import timezone
from urllib.parse import unquote, urlparse

import pymysql
from pymysql.cursors import DictCursor


class DuplicateCompanyError(Exception):
    pass


class CompanyNotFoundError(Exception):
    pass


class MySQLCompanyService:
    def __init__(self, database_url: str):
        parsed = urlparse(database_url)
        self._connection_options = dict(
            host=parsed.hostname, port=parsed.port or 3306,
            user=unquote(parsed.username or ""), password=unquote(parsed.password or ""),
            database=parsed.path.lstrip("/"), charset="utf8mb4",
            cursorclass=DictCursor, autocommit=True, init_command="SET time_zone = '+00:00'",
        )

    def _connect(self):
        return pymysql.connect(**self._connection_options)

    @staticmethod
    def _company(row):
        return {
            "id": row["id"], "name": row["name"],
            "officialSiteUrl": row["official_site_url"],
            "documentEntryUrl": row["document_entry_url"], "notes": row["notes"],
            "createdAt": row["created_at"].replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "updatedAt": row["updated_at"].replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }

    def list(self):
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM company ORDER BY id")
                return [self._company(row) for row in cursor.fetchall()]

    def create(self, data):
        name = " ".join(data["name"].split())
        site = data.get("officialSiteUrl")
        if site:
            parsed = urlparse(site.strip())
            site = f"{parsed.scheme}://{parsed.netloc}"
        with self._connect() as conn:
            with conn.cursor() as cursor:
                try:
                    cursor.execute("INSERT INTO company (name, official_site_url, document_entry_url, notes) VALUES (%s, %s, %s, %s)", (name, site or None, (data.get("documentEntryUrl") or "").strip() or None, (data.get("notes") or "").strip() or None))
                except pymysql.IntegrityError as error:
                    if error.args[0] == 1062:
                        raise DuplicateCompanyError(name) from error
                    raise
                cursor.execute("SELECT * FROM company WHERE id = %s", (cursor.lastrowid,))
                return self._company(cursor.fetchone())

    def update(self, company_id, data):
        name = " ".join(data["name"].split())
        site = data.get("officialSiteUrl")
        if site:
            parsed = urlparse(site.strip())
            site = f"{parsed.scheme}://{parsed.netloc}"
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM company WHERE id = %s", (company_id,))
                current = cursor.fetchone()
                if current is None:
                    raise CompanyNotFoundError(company_id)
                entry = ((data.get("documentEntryUrl") or "").strip() or None) if "documentEntryUrl" in data else current["document_entry_url"]
                notes = (data.get("notes") or "").strip() or None
                try:
                    cursor.execute("UPDATE company SET name = %s, official_site_url = %s, document_entry_url = %s, notes = %s, updated_at = UTC_TIMESTAMP() WHERE id = %s", (name, site or None, entry, notes, company_id))
                except pymysql.IntegrityError as error:
                    if error.args[0] == 1062:
                        raise DuplicateCompanyError(name) from error
                    raise
                cursor.execute("SELECT * FROM company WHERE id = %s", (company_id,))
                row = cursor.fetchone()
                if row is None:
                    raise CompanyNotFoundError(company_id)
                return self._company(row)
