from datetime import datetime, timezone
import json
from datetime import datetime, timezone

from .companies import MySQLCompanyService


FIELDS = {
    'id': 'id', 'company_id': 'companyId', 'company_name': 'companyName',
    'name': 'name', 'sales_status': 'salesStatus', 'sales_status_raw': 'salesStatusRaw',
    'identity_group': 'identityGroup', 'same_name_count': 'sameNameCount',
    'sale_date_raw': 'saleDateRaw', 'previous_sales_status_raw': 'previousSalesStatusRaw',
    'status_fetched_at': 'statusFetchedAt', 'source_url': 'sourceUrl',
    'official_product_url': 'officialProductUrl', 'category_code': 'categoryCode',
    'category_name': 'categoryName', 'official_product_code': 'officialProductCode',
    'filing_number': 'filingNumber', 'confirmed_name': 'confirmedName',
    'confirmed_sales_status': 'confirmedSalesStatus', 'correction_note': 'correctionNote',
    'confirmed_at': 'confirmedAt', 'confirmed_by': 'confirmedBy',
    'effective_integration_status': 'integrationStatus',
    'integration_status_reason': 'integrationStatusReason',
    'integration_last_task_id': 'integrationLastTaskId',
    'integration_status_updated_at': 'integrationStatusUpdatedAt',
}


class ProductNotFoundError(Exception):
    pass


class MySQLProductService(MySQLCompanyService):
    def __init__(self, database_url, crawl_database_url):
        super().__init__(database_url)
        self._crawl = MySQLCompanyService(crawl_database_url)

    def count_by_category(self):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT category_code,COUNT(*) AS n FROM product GROUP BY category_code')
                return {row['category_code']: row['n'] for row in cursor.fetchall()}

    def list(self, company_id=None, status=None, q=None, integration_status=None, limit=None, offset=None, material_type=None, material_status=None, product_id=None):
        sql = """SELECT p.*, c.name AS company_name,
            CASE WHEN p.integration_status IN ('completed', 'partial_failed') THEN
                CASE WHEN (SELECT COUNT(DISTINCT d.type) FROM document d WHERE d.product_id = p.id AND d.link_status = 'current' AND d.type IN ('clause','rate_table','cash_value_table','prospectus')) = 4
                    THEN 'completed' ELSE 'partial_failed' END
                ELSE p.integration_status END AS effective_integration_status
            FROM product p JOIN company c ON c.id = p.company_id"""
        args = []
        conditions = []
        if product_id is not None:
            conditions.append('p.id = %s')
            args.append(product_id)
        if company_id is not None:
            conditions.append('p.company_id = %s')
            args.append(company_id)
        if status is not None:
            if status in ('off_sale', 'disabled'):
                conditions.append("COALESCE(p.confirmed_sales_status, p.sales_status) IN (%s,'stopped')")
                args.append(status)
            else:
                conditions.append('COALESCE(p.confirmed_sales_status, p.sales_status) = %s')
                args.append(status)
        if q and q.strip():
            conditions.append('COALESCE(p.confirmed_name, p.name) LIKE %s')
            args.append('%' + q.strip() + '%')
        if material_status is not None:
            types = ('clause', 'rate_table', 'cash_value_table', 'prospectus')
            selected = (material_type,) if material_type else types
            placeholders = ','.join(['%s'] * len(selected))
            with self._connect() as product_connection:
                with product_connection.cursor() as cursor:
                    cursor.execute(f"SELECT product_id, type FROM document WHERE product_id IS NOT NULL AND link_status = 'current' AND type IN ({placeholders})", selected)
                    links = {(row['product_id'], row['type']) for row in cursor.fetchall()}
            with self._crawl._connect() as crawl_connection:
                with crawl_connection.cursor() as cursor:
                    cursor.execute(f'SELECT product_id, type, status FROM product_document_status WHERE type IN ({placeholders})', selected)
                    statuses = cursor.fetchall()
            if material_status == 'not_run':
                covered = {}
                for product_id, kind in links | {(row['product_id'], row['type']) for row in statuses}:
                    covered.setdefault(product_id, set()).add(kind)
                ids = {product_id for product_id, kinds in covered.items() if len(kinds) == len(selected)}
                if ids:
                    conditions.append('p.id NOT IN (' + ','.join(['%s'] * len(ids)) + ')')
                    args.extend(sorted(ids))
            else:
                ids = {product_id for product_id, _ in links} if material_status == 'downloaded' else {row['product_id'] for row in statuses if row['status'] == material_status and (row['product_id'], row['type']) not in links}
                if ids:
                    conditions.append('p.id IN (' + ','.join(['%s'] * len(ids)) + ')')
                    args.extend(sorted(ids))
                else:
                    conditions.append('FALSE')
        if conditions:
            sql += ' WHERE ' + ' AND '.join(conditions)
        if integration_status is not None:
            sql += ' HAVING effective_integration_status = %s'
            args.append(integration_status)
        sql += ' ORDER BY p.id DESC'
        if limit is not None:
            sql += ' LIMIT %s OFFSET %s'
            args.extend((limit, offset or 0))
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, args)
                items = []
                for row in cursor.fetchall():
                    item = {}
                    for column, field in FIELDS.items():
                        value = row[column]
                        if isinstance(value, datetime):
                            value = value.replace(tzinfo=timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
                        item[field] = value
                    items.append(item)
                return items

    def get(self, product_id):
        items = self.list(product_id=product_id)
        if not items:
            raise ProductNotFoundError(product_id)
        with self._crawl._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT type, status FROM product_document_status WHERE product_id = %s', (product_id,))
                statuses = {row['type']: row['status'] for row in cursor.fetchall()}
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM document WHERE product_id = %s ORDER BY updated_at DESC', (product_id,))
                documents = cursor.fetchall()
                cursor.execute('SELECT association_record_id,sales_status,sales_status_raw,sale_date_raw,status_fetched_at,source_url,category_code,category_name,official_product_code,filing_number FROM product_index_record WHERE product_id=%s ORDER BY status_fetched_at DESC,id', (product_id,))
                association_records = cursor.fetchall()
        snapshots = []
        if documents:
            with self._crawl._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT * FROM document_snapshot WHERE document_id IN (' + ','.join(['%s'] * len(documents)) + ') ORDER BY id DESC', [doc['id'] for doc in documents])
                    snapshots = cursor.fetchall()
        sources = []
        for doc in documents:
            snapshot = next((row for row in snapshots if row['document_id'] == doc['id'] and product_id in json.loads(row['product_ids'])), None)
            fetched = snapshot['fetched_at'] if snapshot else doc['created_at']
            sources.append({
                'id': snapshot['id'] if snapshot else 0, 'documentId': doc['id'],
                'type': doc['type'], 'sourceUrl': doc['source_url'] or '',
                'downloadUrl': doc['download_url'] or '',
                'snapshotKey': snapshot['snapshot_key'] if snapshot else '',
                'fetchedAt': fetched.replace(tzinfo=timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                'linkStatus': doc['link_status'], 'mimeType': doc['mime_type'], 'size': doc['size'],
            })
        current_types = {row['type'] for row in sources if row['linkStatus'] == 'current'}
        labels = [('clause', '条款'), ('rate_table', '费率表'), ('cash_value_table', '现金价值表'), ('prospectus', '说明书')]
        items[0]['materials'] = [{'type': kind, 'label': label, 'status': 'downloaded' if kind in current_types else statuses.get(kind) if statuses.get(kind) in ('not_found', 'download_failed', 'attribution_pending') else 'not_run'} for kind, label in labels]
        items[0]['associationRecords'] = [{
            'associationRecordId': row['association_record_id'], 'salesStatus': row['sales_status'],
            'salesStatusRaw': row['sales_status_raw'], 'saleDateRaw': row['sale_date_raw'],
            'statusFetchedAt': row['status_fetched_at'].replace(tzinfo=timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
            'sourceUrl': row['source_url'], 'categoryCode': row['category_code'],
            'categoryName': row['category_name'], 'officialProductCode': row['official_product_code'],
            'filingNumber': row['filing_number'],
        } for row in association_records]
        items[0]['sources'] = [source for source in sources if source['linkStatus'] == 'current']
        items[0]['history'] = [source for source in sources if source['linkStatus'] == 'historical']
        return items[0]

    def update_correction(self, product_id, data):
        columns = {'name': 'confirmed_name', 'salesStatus': 'confirmed_sales_status', 'correctionNote': 'correction_note'}
        assignments = [columns[key] + ' = %s' for key in data]
        values = [data[key] for key in data]
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT id FROM product WHERE id = %s', (product_id,))
                if cursor.fetchone() is None:
                    raise ProductNotFoundError(product_id)
                cursor.execute('UPDATE product SET ' + ', '.join(assignments) + ", confirmed_at = UTC_TIMESTAMP(), confirmed_by = 'internal-token', updated_at = UTC_TIMESTAMP() WHERE id = %s", values + [product_id])
        return self.get(product_id)
