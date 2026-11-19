from .companies import MySQLCompanyService


FIELDS = {
    'globalConcurrency': 'global_concurrency',
    'domainIntervalMs': 'domain_interval_ms',
    'requestTimeoutMs': 'request_timeout_ms',
    'retryAttempts': 'retry_attempts',
    'indexRefreshMinutes': 'index_refresh_minutes',
    'associationCrawlEnabled': 'association_crawl_enabled',
}
DEFAULTS = {
    'globalConcurrency': 2, 'domainIntervalMs': 1000, 'requestTimeoutMs': 30000,
    'retryAttempts': 3, 'indexRefreshMinutes': 10080, 'associationCrawlEnabled': True,
}


class MySQLSettingsService(MySQLCompanyService):
    def get(self):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM app_setting WHERE id = 1')
                row = cursor.fetchone()
        if row is None:
            return dict(DEFAULTS)
        settings = {field: row[column] for field, column in FIELDS.items()}
        settings['associationCrawlEnabled'] = bool(settings['associationCrawlEnabled'])
        return settings

    def update(self, changes):
        next_settings = {**self.get(), **changes}
        columns = list(FIELDS.values())
        values = [next_settings[field] for field in FIELDS]
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO app_setting (id, ' + ', '.join(columns) + ', updated_at) VALUES (1, ' + ', '.join(['%s'] * len(columns)) + ', UTC_TIMESTAMP()) ON DUPLICATE KEY UPDATE ' + ', '.join(column + ' = VALUES(' + column + ')' for column in columns) + ', updated_at = UTC_TIMESTAMP()', values)
        return next_settings
