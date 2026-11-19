import unittest
from baoxian_api.worker_runtime import build_association


class FixtureAssociationTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixture_mode_paginates_without_network_and_maps_source(self):
        class ForbiddenClient:
            async def request(self, *args, **kwargs): raise AssertionError('fixture 模式不应访问网络')
        class ForbiddenLimiter:
            async def wait(self, *args): raise AssertionError('fixture 模式不需要联网限速')
        adapter = build_association({'ASSOCIATION_SOURCE': 'fixture'}, ForbiddenClient(), ForbiddenLimiter(), None)
        categories = await adapter.list_categories()
        self.assertEqual(len(categories), 5)
        page = await adapter.read_page('PubNewProdTypeCode_00')
        self.assertEqual((page['reportedTotal'], len(page['products']), page['nextCursor']), (12, 10, '2'))
        first = page['products'][0]
        self.assertEqual((first['companyName'], first['productName'], first['salesStatus'], first['sourceRecordId']), ('示例人寿', '福寿终身寿险', 'on_sale', 'T001'))
        second = await adapter.read_page('PubNewProdTypeCode_00', '2')
        self.assertEqual((len(second['products']), second['products'][0]['salesStatus']), (2, 'unknown'))
        self.assertNotIn('nextCursor', second)
        empty = await adapter.read_page('PubNewProdTypeCode_04')
        self.assertEqual((empty['reportedTotal'], empty['products']), (0, []))
