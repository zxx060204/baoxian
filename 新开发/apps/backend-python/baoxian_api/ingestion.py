import hashlib
import json
import unicodedata
from datetime import datetime, timezone

from .companies import MySQLCompanyService
from .database_helpers import qualified_database


def clean_whitespace(value):
    return ' '.join(value.split())


def normalize_product_name(value):
    return clean_whitespace(unicodedata.normalize('NFKC', value)).lower()


class ProductIngestionService(MySQLCompanyService):
    def __init__(self, database_url, reporter=None, crawl_database_url=None):
        super().__init__(database_url)
        self.reporter = reporter
        self.crawl = MySQLCompanyService(crawl_database_url) if crawl_database_url else None

    @staticmethod
    def _source_key(company_id, record, normalized):
        record_id = (record.get('sourceRecordId') or '').strip() or None
        if record_id:
            return 'association:' + record_id, record_id
        fallback = {
            'companyId': company_id,
            'name': normalized,
            'status': record.get('salesStatusRaw'),
            'saleDate': record.get('saleDateRaw'),
            'category': record.get('categoryCode'),
            'officialProductCode': record.get('officialProductCode'),
            'filingNumber': record.get('filingNumber'),
        }
        digest = hashlib.sha256(json.dumps(fallback, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
        return 'fallback:' + digest, None

    def _replace_snapshot_product(self, cursor, crawl_database, document_id, source_id, target_id, target_document_id=None):
        cursor.execute(f'SELECT id,product_ids FROM {crawl_database}.document_snapshot WHERE document_id=%s', (document_id,))
        for snapshot in cursor.fetchall():
            product_ids = json.loads(snapshot['product_ids']) if isinstance(snapshot['product_ids'], str) else snapshot['product_ids']
            product_ids = [target_id if product_id == source_id else product_id for product_id in product_ids]
            product_ids = list(dict.fromkeys(product_ids))
            cursor.execute(f'UPDATE {crawl_database}.document_snapshot SET document_id=%s,product_ids=%s WHERE id=%s', (target_document_id or document_id, json.dumps(product_ids), snapshot['id']))

    def _merge_products(self, cursor, source_id, target_id):
        if source_id == target_id:
            return
        crawl_database = qualified_database(self, self.crawl) if self.crawl else None
        cursor.execute('SELECT * FROM document WHERE product_id=%s ORDER BY id', (source_id,))
        for document in cursor.fetchall():
            cursor.execute('SELECT id FROM document WHERE product_id=%s AND type <=> %s AND sha256=%s LIMIT 1', (target_id, document['type'], document['sha256']))
            duplicate = cursor.fetchone()
            if not crawl_database and (duplicate or document):
                raise ValueError('合并已有资料需要配置运行库，以保留快照引用')
            target_document_id = duplicate['id'] if duplicate else document['id']
            self._replace_snapshot_product(cursor, crawl_database, document['id'], source_id, target_id, target_document_id) if crawl_database else None
            if duplicate:
                cursor.execute('DELETE FROM document WHERE id=%s', (document['id'],))
            else:
                cursor.execute('UPDATE document SET product_id=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s', (target_id, document['id']))
        if crawl_database:
            cursor.execute(f'SELECT type,status,reason,task_id,updated_at FROM {crawl_database}.product_document_status WHERE product_id=%s', (source_id,))
            for row in cursor.fetchall():
                cursor.execute(f"INSERT INTO {crawl_database}.product_document_status (product_id,type,status,reason,task_id,updated_at) VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE status=IF(status='downloaded' OR VALUES(status)='downloaded','downloaded',VALUES(status)),reason=IF(status='downloaded',NULL,VALUES(reason)),task_id=VALUES(task_id),updated_at=GREATEST(updated_at,VALUES(updated_at))", (target_id, row['type'], row['status'], row['reason'], row['task_id'], row['updated_at']))
            cursor.execute(f'DELETE FROM {crawl_database}.product_document_status WHERE product_id=%s', (source_id,))
            cursor.execute(f'INSERT IGNORE INTO {crawl_database}.crawl_task_product (task_id,product_id) SELECT task_id,%s FROM {crawl_database}.crawl_task_product WHERE product_id=%s', (target_id, source_id))
            cursor.execute(f'DELETE FROM {crawl_database}.crawl_task_product WHERE product_id=%s', (source_id,))
        cursor.execute('SELECT confirmed_name,confirmed_sales_status,correction_note,confirmed_at,confirmed_by FROM product WHERE id=%s', (source_id,))
        source = cursor.fetchone()
        if source:
            cursor.execute("UPDATE product SET confirmed_name=COALESCE(confirmed_name,%s),confirmed_sales_status=COALESCE(confirmed_sales_status,%s),correction_note=COALESCE(correction_note,%s),confirmed_at=COALESCE(confirmed_at,%s),confirmed_by=COALESCE(confirmed_by,%s),integration_status=IF(integration_status IN ('completed','partial_failed'),integration_status,'not_started'),integration_status_reason=NULL,integration_last_task_id=NULL,updated_at=UTC_TIMESTAMP() WHERE id=%s", (source['confirmed_name'], source['confirmed_sales_status'], source['correction_note'], source['confirmed_at'], source['confirmed_by'], target_id))
        cursor.execute('UPDATE product_index_record SET product_id=%s WHERE product_id=%s', (target_id, source_id))
        cursor.execute('DELETE FROM product WHERE id=%s', (source_id,))

    @staticmethod
    def _sync_product(cursor, product_id, normalized, identity_group, previous_raw=None):
        cursor.execute('SELECT * FROM product_index_record WHERE product_id=%s ORDER BY status_fetched_at DESC,id DESC', (product_id,))
        sources = cursor.fetchall()
        if not sources:
            return
        latest = sources[0]
        count = len(sources)
        status = 'stopped' if identity_group == 'stopped' else latest['sales_status']
        raw_status = '停售/停用' if identity_group == 'stopped' else latest['sales_status_raw']
        association_record_id = latest['association_record_id'] if count == 1 else None
        sale_date = latest['sale_date_raw'] if count == 1 else None
        code = latest['official_product_code'] if count == 1 else None
        filing = latest['filing_number'] if count == 1 else None
        cursor.execute('UPDATE product SET name=%s,normalized_name=%s,identity_group=%s,same_name_count=%s,sales_status=%s,sales_status_raw=%s,status_fetched_at=%s,source_url=%s,category_code=%s,category_name=%s,association_record_id=%s,official_product_code=%s,filing_number=%s,sale_date_raw=%s,previous_sales_status_raw=COALESCE(%s,previous_sales_status_raw),updated_at=UTC_TIMESTAMP() WHERE id=%s', (latest['name'], normalized, identity_group, count, status, raw_status, latest['status_fetched_at'], latest['source_url'], latest['category_code'], latest['category_name'], association_record_id, code, filing, sale_date, previous_raw, product_id))

    @staticmethod
    def _refresh_same_name_count(cursor, company_id, normalized):
        cursor.execute('SELECT COUNT(*) AS n FROM product_index_record WHERE company_id=%s AND normalized_name=%s', (company_id, normalized))
        count = cursor.fetchone()['n']
        cursor.execute('UPDATE product SET same_name_count=%s WHERE company_id=%s AND normalized_name=%s', (count, company_id, normalized))

    def upsert(self, record, category, task_id=None):
        company_name = clean_whitespace(record['companyName'])
        if not company_name:
            raise ValueError('公司名称不能为空')
        name = clean_whitespace(record['productName'])
        normalized = normalize_product_name(name)
        fetched = datetime.fromisoformat(record['fetchedAt'].replace('Z', '+00:00')).astimezone(timezone.utc).replace(tzinfo=None)
        connection = self._connect()
        cursor = connection.cursor()
        try:
            connection.begin()
            cursor.execute('SELECT id FROM company WHERE name=%s LIMIT 1', (company_name,))
            company = cursor.fetchone()
            if company is None:
                cursor.execute('INSERT INTO company (name) VALUES (%s)', (company_name,))
                company_id = cursor.lastrowid
            else:
                company_id = company['id']
            source_key, record_id = self._source_key(company_id, record, normalized)
            stopped = record['salesStatus'] in ('off_sale', 'disabled')
            identity_group = 'stopped' if stopped else 'individual'
            incoming = {
                'companyId': company_id, 'companyName': company_name, 'name': name,
                'normalizedName': normalized, 'salesStatus': 'stopped' if stopped else record['salesStatus'],
                'salesStatusRaw': '停售/停用' if stopped else record.get('salesStatusRaw'),
                'fetchedAt': record['fetchedAt'], 'sourceUrl': record['sourceUrl'], 'category': category,
            }
            for key in ('saleDateRaw', 'sourceRecordId', 'officialProductCode', 'filingNumber'):
                if key in record:
                    incoming[key] = record[key]

            cursor.execute('SELECT * FROM product_index_record WHERE company_id=%s AND source_key=%s LIMIT 1', (company_id, source_key))
            source_record = cursor.fetchone()
            old_product_id = source_record['product_id'] if source_record else None
            old_source_name = source_record['normalized_name'] if source_record else None
            old_raw_status = source_record['sales_status_raw'] if source_record else None
            old_product = None
            if old_product_id:
                cursor.execute('SELECT * FROM product WHERE id=%s', (old_product_id,))
                old_product = cursor.fetchone()
            if not source_record and record_id:
                cursor.execute('SELECT * FROM product WHERE company_id=%s AND association_record_id=%s LIMIT 1', (company_id, record_id))
                old_product = cursor.fetchone()
                if old_product:
                    old_product_id = old_product['id']
            cursor.execute('SELECT * FROM product WHERE company_id=%s AND normalized_name=%s ORDER BY id', (company_id, normalized))
            candidates = cursor.fetchall()

            if not source_record and not record_id and not stopped and len(candidates) > 1:
                connection.rollback()
                fields = {'id': 'id', 'name': 'name', 'sales_status': 'salesStatus', 'sales_status_raw': 'salesStatusRaw', 'association_record_id': 'associationRecordId', 'confirmed_name': 'confirmedName', 'confirmed_sales_status': 'confirmedSalesStatus'}
                return {'status': 'ambiguous', 'incoming': incoming, 'candidates': [{target: candidate[source] for source, target in fields.items()} for candidate in candidates]}
            if not source_record and not record_id and not stopped and len(candidates) == 1:
                cursor.execute('SELECT * FROM product_index_record WHERE product_id=%s ORDER BY id', (candidates[0]['id'],))
                prior_sources = cursor.fetchall()
                if len(prior_sources) == 1:
                    source_record = prior_sources[0]
                    source_key = source_record['source_key']
                    record_id = source_record['association_record_id']
                    old_product_id = source_record['product_id']
                    old_source_name = source_record['normalized_name']
                    old_raw_status = source_record['sales_status_raw']
                    old_product = candidates[0]
            if not source_record and record_id and candidates and all(candidate['identity_group'] != 'stopped' and not candidate['association_record_id'] for candidate in candidates) and not stopped:
                connection.rollback()
                fields = {'id': 'id', 'name': 'name', 'sales_status': 'salesStatus', 'sales_status_raw': 'salesStatusRaw', 'association_record_id': 'associationRecordId', 'confirmed_name': 'confirmedName', 'confirmed_sales_status': 'confirmedSalesStatus'}
                return {'status': 'ambiguous', 'incoming': incoming, 'candidates': [{target: candidate[source] for source, target in fields.items()} for candidate in candidates]}

            if stopped:
                cursor.execute("SELECT * FROM product WHERE company_id=%s AND normalized_name=%s AND identity_group='stopped' ORDER BY id LIMIT 1", (company_id, normalized))
                target = cursor.fetchone()
                if target is None and old_product and old_product['identity_group'] == 'individual':
                    cursor.execute('SELECT COUNT(*) AS n FROM product_index_record WHERE product_id=%s', (old_product_id,))
                    if cursor.fetchone()['n'] <= 1:
                        target = old_product
            elif old_product:
                cursor.execute('SELECT COUNT(*) AS n FROM product_index_record WHERE product_id=%s', (old_product_id,))
                target = old_product if cursor.fetchone()['n'] <= 1 else None
            else:
                target = None

            if target is None:
                fields = {
                    'company_id': company_id, 'name': name, 'normalized_name': normalized,
                    'identity_group': identity_group, 'same_name_count': 1,
                    'sales_status': 'stopped' if stopped else record['salesStatus'],
                    'sales_status_raw': '停售/停用' if stopped else record.get('salesStatusRaw'),
                    'status_fetched_at': fetched, 'source_url': record['sourceUrl'],
                    'category_code': category['code'], 'category_name': category['name'],
                    'association_record_id': record_id, 'official_product_code': record.get('officialProductCode'),
                    'filing_number': record.get('filingNumber'), 'sale_date_raw': record.get('saleDateRaw'),
                }
                cursor.execute('INSERT INTO product (' + ','.join(fields) + ') VALUES (' + ','.join(['%s'] * len(fields)) + ')', list(fields.values()))
                product_id = cursor.lastrowid
                result_status = 'inserted'
                target_before = None
            else:
                product_id = target['id']
                result_status = 'updated'
                target_before = target
                if old_product_id and old_product_id != product_id:
                    cursor.execute('SELECT COUNT(*) AS n FROM product_index_record WHERE product_id=%s', (old_product_id,))
                    if cursor.fetchone()['n'] <= 1:
                        self._merge_products(cursor, old_product_id, product_id)

            source_fields = {
                'company_id': company_id, 'product_id': product_id, 'source_key': source_key,
                'association_record_id': record_id, 'name': name, 'normalized_name': normalized,
                'sales_status': record['salesStatus'], 'sales_status_raw': record.get('salesStatusRaw'),
                'sale_date_raw': record.get('saleDateRaw', source_record['sale_date_raw'] if source_record else None), 'status_fetched_at': fetched,
                'source_url': record['sourceUrl'], 'category_code': category['code'],
                'category_name': category['name'], 'official_product_code': record.get('officialProductCode', source_record['official_product_code'] if source_record else None),
                'filing_number': record.get('filingNumber', source_record['filing_number'] if source_record else None),
            }
            if source_record:
                updates = {key: value for key, value in source_fields.items() if key not in ('company_id', 'source_key')}
                cursor.execute('UPDATE product_index_record SET ' + ','.join(key + '=%s' for key in updates) + ',updated_at=UTC_TIMESTAMP() WHERE id=%s', [*updates.values(), source_record['id']])
            else:
                cursor.execute('INSERT INTO product_index_record (' + ','.join(source_fields) + ') VALUES (' + ','.join(['%s'] * len(source_fields)) + ')', list(source_fields.values()))

            self._sync_product(cursor, product_id, normalized, identity_group, old_raw_status if old_raw_status and old_raw_status != record.get('salesStatusRaw') else None)
            if old_product_id and old_product_id != product_id:
                cursor.execute('SELECT identity_group,normalized_name FROM product WHERE id=%s', (old_product_id,))
                remaining_group = cursor.fetchone()
                if remaining_group:
                    cursor.execute('SELECT id FROM product_index_record WHERE product_id=%s ORDER BY id LIMIT 1', (old_product_id,))
                    remaining_source = cursor.fetchone()
                    if remaining_source:
                        self._sync_product(cursor, old_product_id, remaining_group['normalized_name'], remaining_group['identity_group'])
            self._refresh_same_name_count(cursor, company_id, normalized)
            if old_source_name and old_source_name != normalized:
                self._refresh_same_name_count(cursor, company_id, old_source_name)
            cursor.execute('SELECT * FROM product WHERE id=%s', (product_id,))
            current = cursor.fetchone()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

        self._report(target_before or current, incoming, task_id)
        if old_raw_status and old_raw_status != record.get('salesStatusRaw') and self.reporter is not None:
            self.reporter.record_source_value_change(task_id, product_id, old_raw_status or '', record.get('salesStatusRaw') or '')
        return {'status': result_status, 'productId': product_id}

    def _report(self, existing, incoming, task_id):
        if self.reporter is None or existing is None:
            return
        for field, column, key in (('name', 'confirmed_name', 'name'), ('salesStatus', 'confirmed_sales_status', 'salesStatus')):
            confirmed = existing[column]
            collected = incoming[key]
            if field == 'salesStatus' and confirmed in ('off_sale', 'disabled') and collected == 'stopped':
                continue
            if confirmed and confirmed != collected:
                self.reporter.record_correction_diff(task_id, existing['id'], field, confirmed, collected)
