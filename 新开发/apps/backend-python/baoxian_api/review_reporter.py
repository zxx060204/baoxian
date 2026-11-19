import json
from urllib.parse import urlsplit
from datetime import datetime, timezone

from .companies import MySQLCompanyService
from .ingestion import ProductIngestionService, normalize_product_name
from .tasks import public_row


class ReviewItemNotFoundError(Exception): pass
class ReviewItemAlreadyResolvedError(Exception): pass
class InvalidReviewResolutionError(Exception): pass


class MySQLReviewReporter(MySQLCompanyService):
    def __init__(self, database_url, product_database_url=None):
        super().__init__(database_url)
        self.product = MySQLCompanyService(product_database_url) if product_database_url else None
        self.product_database_url = product_database_url
        self.crawl_database_url = database_url

    def _product_table(self, table):
        if self.product is None: raise RuntimeError('缺少产品数据库配置')
        product_options = self.product._connection_options
        if any(self._connection_options[key] != product_options[key] for key in ('host', 'port', 'user')):
            raise RuntimeError('产品库和运行库需位于同一 MySQL 实例并使用相同用户，才能原子处理待确认项')
        database = product_options['database'].replace('\x60', '\x60\x60')
        return '\x60' + database + '\x60.\x60' + table + '\x60'

    def resolve(self, item_id, resolution, operator='internal-token'):
        with self._connect() as connection:
            try:
                connection.begin()
                with connection.cursor() as cursor:
                    cursor.execute('SELECT * FROM review_item WHERE id=%s FOR UPDATE', (item_id,))
                    item = cursor.fetchone()
                    if item is None: raise ReviewItemNotFoundError(item_id)
                    if item['status'] != 'pending': raise ReviewItemAlreadyResolvedError(item_id)
                    payload = json.loads(item['payload']) if isinstance(item['payload'], str) else item['payload']
                    action = resolution['action']
                    kind = payload.get('kind')
                    resolved_product_id = None
                    product = None
                    if action == 'ignore' and kind in ('company_site', 'document_attribution', 'correction_diff', 'source_value_diff'):
                        status = 'dismissed'
                    elif kind == 'company_site' and action == 'select_site':
                        url = resolution.get('siteUrl')
                        if not any(candidate.get('url') == url for candidate in payload.get('siteCandidates', [])):
                            raise InvalidReviewResolutionError('官网候选不存在')
                        try:
                            parsed = urlsplit(url)
                            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                                raise ValueError()
                            origin = 'https://' + parsed.netloc
                        except (TypeError, ValueError):
                            raise InvalidReviewResolutionError('官网候选地址无效')
                        cursor.execute('UPDATE ' + self._product_table('company') + ' SET official_site_url=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s', (origin, payload['companyId']))
                        status = 'resolved'
                    elif kind == 'document_attribution' and action == 'assign':
                        document_id, source_id = payload.get('documentId'), payload.get('sourceId')
                        if not document_id or not source_id: raise InvalidReviewResolutionError('该待确认项缺少已保存文件')
                        resolved_product_id = resolution['productId']
                        product_table, document_table = self._product_table('product'), self._product_table('document')
                        cursor.execute('SELECT * FROM ' + product_table + ' WHERE id=%s', (resolved_product_id,))
                        product_row = cursor.fetchone()
                        if not product_row: raise InvalidReviewResolutionError('指定产品不存在')
                        product = public_row(product_row)
                        cursor.execute('SELECT * FROM document_snapshot WHERE id=%s FOR UPDATE', (source_id,))
                        snapshot = cursor.fetchone()
                        if not snapshot or snapshot['document_id'] != document_id: raise InvalidReviewResolutionError('该待确认项缺少对应快照')
                        cursor.execute('SELECT * FROM ' + document_table + ' WHERE id=%s FOR UPDATE', (document_id,))
                        original = cursor.fetchone()
                        if not original: raise InvalidReviewResolutionError('该待确认项缺少已保存文件')
                        candidate = payload['candidate']
                        historical = candidate.get('versionStatus') == 'historical'
                        cursor.execute('SELECT id FROM ' + document_table + ' WHERE product_id=%s AND type=%s AND sha256=%s LIMIT 1', (resolved_product_id, candidate['type'], original['sha256']))
                        existing = cursor.fetchone()
                        actual_id = existing['id'] if existing else None
                        if actual_id is None and original['product_id'] is None:
                            cursor.execute('UPDATE ' + document_table + ' SET product_id=%s,type=%s,link_status=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s AND product_id IS NULL', (resolved_product_id, candidate['type'], 'historical' if historical else 'current', document_id))
                            if cursor.rowcount: actual_id = document_id
                        if actual_id is None:
                            cursor.execute('INSERT INTO ' + document_table + ' (product_id,type,link_status,sha256,filename,mime_type,size,storage_key,source_url,download_url,page_title) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (resolved_product_id, candidate['type'], 'historical' if historical else 'current', original['sha256'], original['filename'], original['mime_type'], original['size'], original['storage_key'], candidate.get('officialProductUrl') or original['source_url'], candidate['downloadUrl'], candidate['title']))
                            actual_id = cursor.lastrowid
                        if actual_id != document_id:
                            cursor.execute('INSERT INTO document_snapshot (task_id,document_id,product_ids,source_url,download_url,snapshot_key,snapshot_kind,page_title,supporting_text,fetched_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (snapshot['task_id'], actual_id, json.dumps([resolved_product_id]), snapshot['source_url'], snapshot['download_url'], snapshot['snapshot_key'], snapshot['snapshot_kind'], snapshot['page_title'], snapshot['supporting_text'], snapshot['fetched_at']))
                        official = candidate.get('officialProductUrl')
                        if official:
                            parsed = urlsplit(official)
                            if parsed.scheme and parsed.netloc and (parsed.path not in ('', '/') or parsed.query):
                                cursor.execute('UPDATE ' + product_table + ' SET official_product_url=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s', (official, resolved_product_id))
                        if not historical:
                            cursor.execute("UPDATE " + document_table + " SET link_status='historical',updated_at=UTC_TIMESTAMP() WHERE product_id=%s AND type=%s AND download_url=%s AND id<>%s", (resolved_product_id, candidate['type'], candidate['downloadUrl'], actual_id))
                        cursor.execute('UPDATE ' + document_table + ' SET link_status=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s', ('historical' if historical else 'current', actual_id))
                        if not historical:
                            cursor.execute("INSERT INTO product_document_status (product_id,type,status,task_id) VALUES (%s,%s,'downloaded',%s) ON DUPLICATE KEY UPDATE status='downloaded',reason=NULL,task_id=VALUES(task_id),updated_at=UTC_TIMESTAMP()", (resolved_product_id, candidate['type'], item['task_id']))
                        product_ids = json.loads(snapshot['product_ids']) if isinstance(snapshot['product_ids'], str) else snapshot['product_ids']
                        if resolved_product_id not in product_ids:
                            cursor.execute('UPDATE document_snapshot SET product_ids=%s WHERE id=%s', (json.dumps([*product_ids, resolved_product_id]), source_id))
                        status = 'resolved'
                    elif kind == 'product_identity' and action in ('merge', 'new'):
                        if self.product is None: raise RuntimeError('缺少产品数据库配置')
                        incoming = payload['incoming']
                        if action == 'merge' and not any(candidate.get('id') == resolution.get('productId') for candidate in payload['candidates']):
                            raise InvalidReviewResolutionError('候选产品不存在')
                        fetched = datetime.fromisoformat(incoming['fetchedAt'].replace('Z', '+00:00')).astimezone(timezone.utc).replace(tzinfo=None)
                        stopped = incoming['salesStatus'] in ('off_sale', 'disabled')
                        identity_group = 'stopped' if stopped else 'individual'
                        fields = {'company_id': incoming['companyId'], 'name': incoming['name'], 'normalized_name': incoming['normalizedName'], 'identity_group': identity_group, 'same_name_count': 1, 'sales_status': 'stopped' if stopped else incoming['salesStatus'], 'sales_status_raw': '停售/停用' if stopped else incoming['salesStatusRaw'], 'status_fetched_at': fetched, 'source_url': incoming['sourceUrl'], 'category_code': incoming['category']['code'], 'category_name': incoming['category']['name'], 'association_record_id': incoming.get('sourceRecordId'), 'official_product_code': incoming.get('officialProductCode'), 'filing_number': incoming.get('filingNumber')}
                        if 'saleDateRaw' in incoming: fields['sale_date_raw'] = incoming['saleDateRaw']
                        product_table = self._product_table('product')
                        if action == 'merge':
                            resolved_product_id = resolution['productId']
                            cursor.execute('SELECT id FROM ' + product_table + ' WHERE id=%s', (resolved_product_id,))
                            if not cursor.fetchone(): raise InvalidReviewResolutionError('候选产品不存在')
                            cursor.execute('UPDATE ' + product_table + ' SET ' + ','.join(column + '=%s' for column in fields) + ',updated_at=UTC_TIMESTAMP() WHERE id=%s', (*fields.values(), resolved_product_id))
                        else:
                            cursor.execute('INSERT INTO ' + product_table + ' (' + ','.join(fields) + ') VALUES (' + ','.join(['%s'] * len(fields)) + ')', tuple(fields.values()))
                            resolved_product_id = cursor.lastrowid
                        cursor.execute('SELECT * FROM ' + product_table + ' WHERE id=%s', (resolved_product_id,))
                        product = public_row(cursor.fetchone())
                        source_record = {
                            'salesStatus': incoming['salesStatus'], 'salesStatusRaw': incoming.get('salesStatusRaw'),
                            'saleDateRaw': incoming.get('saleDateRaw'), 'sourceRecordId': incoming.get('sourceRecordId'),
                            'categoryCode': incoming['category']['code'], 'officialProductCode': incoming.get('officialProductCode'),
                            'filingNumber': incoming.get('filingNumber'),
                        }
                        source_key, source_id = ProductIngestionService._source_key(incoming['companyId'], source_record, incoming['normalizedName'])
                        source_fields = {'company_id': incoming['companyId'], 'product_id': resolved_product_id, 'source_key': source_key, 'association_record_id': source_id, 'name': incoming['name'], 'normalized_name': incoming['normalizedName'], 'sales_status': incoming['salesStatus'], 'sales_status_raw': incoming.get('salesStatusRaw'), 'sale_date_raw': incoming.get('saleDateRaw'), 'status_fetched_at': fetched, 'source_url': incoming['sourceUrl'], 'category_code': incoming['category']['code'], 'category_name': incoming['category']['name'], 'official_product_code': incoming.get('officialProductCode'), 'filing_number': incoming.get('filingNumber')}
                        source_table = self._product_table('product_index_record')
                        cursor.execute('INSERT INTO ' + source_table + ' (' + ','.join(source_fields) + ') VALUES (' + ','.join(['%s'] * len(source_fields)) + ') ON DUPLICATE KEY UPDATE product_id=VALUES(product_id),name=VALUES(name),normalized_name=VALUES(normalized_name),sales_status=VALUES(sales_status),sales_status_raw=VALUES(sales_status_raw),sale_date_raw=VALUES(sale_date_raw),status_fetched_at=VALUES(status_fetched_at),source_url=VALUES(source_url),category_code=VALUES(category_code),category_name=VALUES(category_name),official_product_code=VALUES(official_product_code),filing_number=VALUES(filing_number),updated_at=UTC_TIMESTAMP()', tuple(source_fields.values()))
                        cursor.execute('SELECT COUNT(*) AS n FROM ' + source_table + ' WHERE product_id=%s', (resolved_product_id,))
                        source_count = cursor.fetchone()['n']
                        cursor.execute('SELECT COUNT(*) AS n FROM ' + source_table + ' WHERE company_id=%s AND normalized_name=%s', (incoming['companyId'], incoming['normalizedName']))
                        same_name_count = cursor.fetchone()['n']
                        cursor.execute('UPDATE ' + product_table + ' SET same_name_count=%s,association_record_id=%s,sale_date_raw=%s,official_product_code=%s,filing_number=%s WHERE id=%s', (same_name_count, source_id if source_count == 1 else None, incoming.get('saleDateRaw') if source_count == 1 else None, incoming.get('officialProductCode') if source_count == 1 else None, incoming.get('filingNumber') if source_count == 1 else None, resolved_product_id))
                        cursor.execute('UPDATE ' + product_table + ' SET same_name_count=%s WHERE company_id=%s AND normalized_name=%s', (same_name_count, incoming['companyId'], incoming['normalizedName']))
                        cursor.execute('SELECT * FROM ' + product_table + ' WHERE id=%s', (resolved_product_id,))
                        product = public_row(cursor.fetchone())
                        status = 'resolved'
                    elif kind in ('correction_diff', 'source_value_diff') and action in ('adopt', 'keep'):
                        resolved_product_id = payload['productId']
                        product_table = self._product_table('product')
                        cursor.execute('SELECT * FROM ' + product_table + ' WHERE id=%s', (resolved_product_id,))
                        if not cursor.fetchone(): raise InvalidReviewResolutionError('差异对应产品不存在')
                        if action == 'adopt':
                            field = payload['field']
                            value = payload['collectedValue']
                            if field == 'name':
                                column = 'confirmed_name'
                            else:
                                column = 'confirmed_sales_status'
                                value = {'在售': 'on_sale', 'on_sale': 'on_sale', '停售': 'off_sale', 'off_sale': 'off_sale', '停用': 'disabled', 'disabled': 'disabled', '停售/停用': 'stopped', 'stopped': 'stopped'}.get(value, 'unknown')
                            cursor.execute('UPDATE ' + product_table + ' SET ' + column + '=%s,confirmed_at=UTC_TIMESTAMP(),confirmed_by=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s', (value, operator, resolved_product_id))
                        cursor.execute('SELECT * FROM ' + product_table + ' WHERE id=%s', (resolved_product_id,))
                        product = public_row(cursor.fetchone())
                        status = 'resolved'
                    else:
                        raise InvalidReviewResolutionError('该处理动作暂不支持或不适用于此待确认项')
                    cursor.execute('UPDATE review_item SET status=%s,resolution=%s,resolved_product_id=%s,resolved_by=%s,resolved_at=UTC_TIMESTAMP(),updated_at=UTC_TIMESTAMP() WHERE id=%s', (status, action, resolved_product_id, operator, item_id))
                    cursor.execute('SELECT * FROM review_item WHERE id=%s', (item_id,))
                    result = public_row(cursor.fetchone())
                    if isinstance(result['payload'], str): result['payload'] = json.loads(result['payload'])
                connection.commit()
                response = {'item': result}
                if product is not None: response['product'] = product
                return response
            except BaseException:
                connection.rollback()
                raise

    def list(self, status=None, item_type=None):
        filters = [(column, value) for column, value in (('status', status), ('type', item_type)) if value is not None]
        sql = 'SELECT * FROM review_item'
        if filters: sql += ' WHERE ' + ' AND '.join(column + '=%s' for column, _ in filters)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql + ' ORDER BY id DESC', tuple(value for _, value in filters))
                items = [public_row(row) for row in cursor.fetchall()]
        for item in items:
            if isinstance(item['payload'], str): item['payload'] = json.loads(item['payload'])
        return items

    def _record(self, task_id, item_type, payload, deduplicate=False):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                if deduplicate:
                    cursor.execute("SELECT * FROM review_item WHERE status = 'pending' AND type = %s", (item_type,))
                    for row in cursor.fetchall():
                        existing = json.loads(row['payload']) if isinstance(row['payload'], str) else row['payload']
                        if existing == payload:
                            item = public_row(row)
                            item['payload'] = existing
                            return item
                cursor.execute("INSERT INTO review_item (type, status, task_id, payload) VALUES (%s,'pending',%s,%s)", (item_type, task_id, json.dumps(payload, ensure_ascii=False)))
                cursor.execute('SELECT * FROM review_item WHERE id = %s', (cursor.lastrowid,))
                item = public_row(cursor.fetchone())
                if isinstance(item['payload'], str):
                    item['payload'] = json.loads(item['payload'])
                return item

    def record_product_identity(self, task_id, incoming, candidates):
        return self._record(task_id, 'product_identity', {'kind': 'product_identity', 'incoming': incoming, 'candidates': candidates})

    def record_correction_diff(self, task_id, product_id, field, confirmed_value, collected_value):
        payload = {'kind': 'correction_diff', 'productId': product_id, 'field': field, 'confirmedValue': confirmed_value, 'collectedValue': collected_value}
        return self._record(task_id, 'sales_status_diff' if field == 'salesStatus' else 'correction_diff', payload, deduplicate=True)

    def record_source_value_change(self, task_id, product_id, previous_value, collected_value):
        payload = {'kind': 'source_value_diff', 'productId': product_id, 'field': 'salesStatus', 'confirmedValue': previous_value, 'collectedValue': collected_value}
        return self._record(task_id, 'source_value_diff', payload, deduplicate=True)

    def record_company_site(self, task_id, company_id, company_name, candidates, reason=None):
        payload = {'kind': 'company_site', 'companyId': company_id, 'companyName': company_name, 'siteCandidates': candidates}
        if reason:
            payload['reason'] = reason
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM review_item WHERE status='pending' AND type='company_site'")
                for row in cursor.fetchall():
                    existing = json.loads(row['payload']) if isinstance(row['payload'], str) else row['payload']
                    if existing.get('companyId') == company_id:
                        cursor.execute('UPDATE review_item SET payload=%s,task_id=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s', (json.dumps(payload, ensure_ascii=False), task_id, row['id']))
                        item = public_row(row)
                        item['payload'], item['taskId'] = payload, task_id
                        return item
        return self._record(task_id, 'company_site', payload)

    def record_document_attribution(self, task_id, candidate, document_id=None, source_id=None):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM review_item WHERE status='pending' AND type='document_attribution'")
                for row in cursor.fetchall():
                    payload = json.loads(row['payload']) if isinstance(row['payload'], str) else row['payload']
                    existing = payload.get('candidate', {})
                    if existing.get('downloadUrl') == candidate['downloadUrl'] and existing.get('sourcePageUrl') == candidate['sourcePageUrl']:
                        item = public_row(row)
                        item['payload'] = payload
                        return item
        payload = {'kind': 'document_attribution', 'candidate': candidate}
        if document_id: payload['documentId'] = document_id
        if source_id: payload['sourceId'] = source_id
        return self._record(task_id, 'document_attribution', payload)
