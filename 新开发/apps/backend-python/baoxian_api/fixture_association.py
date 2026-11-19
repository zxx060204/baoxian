RECORDS = {
    'PubNewProdTypeCode_00': [
        {'companyName': '示例人寿', 'productName': '福寿终身寿险', 'salesStatusRaw': '在售', 'recordId': 'T001', 'officialProductCode': 'P001', 'filingNumber': '备案-2024-001'},
        *[{'companyName': '示例人寿', 'productName': name, 'salesStatusRaw': status} for name, status in (('福寿年金保险','在售'), ('安康医疗保险','停售'), ('安心意外险',None), ('福寿两全保险','在售'), ('康宁重疾险','停售'), ('金禧年金保险','在售'), ('守护定期寿险','在售'))],
        *[{'companyName': '示例健康', 'productName': name, 'salesStatusRaw': status} for name, status in (('健康无忧医疗险','在售'), ('健康人生重疾险','停售'), ('意外守护险','暂停销售'), ('住院津贴险',None))],
    ],
    'PubNewProdTypeCode_01': [
        {'companyName': '示例人寿', 'productName': '分红终身寿险', 'salesStatusRaw': '在售'},
        {'companyName': '示例人寿', 'productName': '万能年金保险', 'salesStatusRaw': '在售'},
        {'companyName': '示例健康', 'productName': '护理保险', 'salesStatusRaw': '停售'},
    ],
}


class FixtureAssociationGateway:
    async def list_categories(self):
        names = ('人寿保险', '年金保险', '健康保险', '意外伤害保险', '委托管理业务')
        return [{'code': f'PubNewProdTypeCode_{index:02d}', 'name': name} for index, name in enumerate(names)]

    async def list_product_page(self, category, page, page_size):
        records = RECORDS.get(category, [])
        start = (page - 1) * page_size
        return {'records': records[start:start + page_size], 'total': len(records), 'sourceUrl': f'https://example.test/association/{category}?page={page}'}
