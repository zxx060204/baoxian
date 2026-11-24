import asyncio
import json
import re
from decimal import ROUND_HALF_UP, Decimal

from .documents import SavedLayoutNotFoundError
from .products import ProductNotFoundError


class BenefitIllustrationError(Exception):
    pass


class BenefitDataMissingError(BenefitIllustrationError):
    pass


class ParameterOutOfRangeError(BenefitIllustrationError):
    pass


class UnsupportedProductTemplateError(BenefitIllustrationError):
    pass


GENDER_LABELS = {'male': '男性', 'female': '女性'}
PAYMENT_LABELS = {'single': '趸交', '3': '3年交', '5': '5年交', '10': '10年交'}
PAYMENT_YEARS = {'single': 1, '3': 3, '5': 5, '10': 10}
COVERAGE_LABELS = {'to_60': '保至60岁', 'to_70': '保至70岁', 'to_80': '保至80岁', 'to_90': '保至90岁'}
COVERAGE_AGES = {'to_60': 60, 'to_70': 70, 'to_80': 80, 'to_90': 90}

_PAY_PATTERN = re.compile(r'^(趸交|\d+年交)$')
_PERIOD_PATTERN = re.compile(r'^保至\s*(\d+)\s*岁$')
_CASH_LINE_PATTERN = re.compile(r'^(\S+)\s+(趸交|\d+年交)\s+(男性|女性)\s+(\d+)\s+(\d+)\s+(-?[\d,]+(?:\.\d+)?)$')


def _cell_text(cell):
    if cell is None or cell == -1:
        return ''
    if isinstance(cell, (list, tuple)):
        return str(cell[0]).strip() if cell else ''
    return str(cell).strip()


def _cell_span(cell):
    if isinstance(cell, (list, tuple)) and len(cell) >= 3:
        try:
            return max(1, int(cell[2]))
        except (TypeError, ValueError):
            return 1
    return 1


def _parse_number(text):
    cleaned = text.replace(',', '').strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _round_yuan(value):
    return int(Decimal(str(value)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _period_label(text):
    match = _PERIOD_PATTERN.match(text.replace(' ', ''))
    return f'保至{match.group(1)}岁' if match else None


class RateTable:
    def __init__(self, entries):
        self._entries = entries

    def lookup(self, period, gender, payment, age):
        return self._entries.get((period, gender, payment, age))


def _rate_column_map(grid):
    pay_row = None
    for index, row in enumerate(grid):
        if any(_PAY_PATTERN.match(_cell_text(cell)) for cell in row):
            pay_row = index
            break
    if pay_row is None or pay_row == 0:
        return None, None
    columns = {}
    col = 0
    for cell in grid[pay_row - 1]:
        span = _cell_span(cell)
        text = _cell_text(cell)
        if text in ('男性', '女性'):
            for j in range(col, col + span):
                columns[j] = {'gender': text}
        col += span
    col = 0
    for cell in grid[pay_row]:
        span = _cell_span(cell)
        text = _cell_text(cell)
        if _PAY_PATTERN.match(text) and col in columns:
            for j in range(col, col + span):
                if j in columns:
                    columns[j]['payment'] = text
        col += span
    columns = {j: value for j, value in columns.items() if 'payment' in value}
    return columns, pay_row + 1


def parse_rate_layout(layout):
    markers = []
    for context in layout.get('contexts', []):
        label = _period_label(context.get('text') or '')
        if label:
            bbox = context.get('bbox') or [0, 0]
            markers.append((context.get('page', 0), bbox[1], label))
    markers.sort()

    tables = sorted(layout.get('tables', []), key=lambda t: (t.get('page', 0), (t.get('bbox') or [0, 0])[1]))
    entries = {}
    section_label = None
    columns = None
    for table in tables:
        grid = table.get('grid') or []
        if not grid:
            continue
        title = _period_label(_cell_text(grid[0][0]))
        new_columns, data_start = _rate_column_map(grid)
        if new_columns:
            columns = new_columns
            if title:
                section_label = title
            else:
                page, y = table.get('page', 0), (table.get('bbox') or [0, 0])[1]
                before = [label for m_page, m_y, label in markers if (m_page, m_y) <= (page, y)]
                section_label = before[-1] if before else section_label
            rows = grid[data_start:]
        else:
            rows = grid
        if section_label is None or columns is None:
            continue
        for row in rows:
            age_text = _cell_text(row[0]) if row else ''
            if not age_text.isdigit():
                continue
            age = int(age_text)
            for j, mapping in columns.items():
                if j >= len(row):
                    continue
                value = _parse_number(_cell_text(row[j]))
                if value is not None:
                    entries[(section_label, mapping['gender'], mapping['payment'], age)] = value
    return RateTable(entries)


class CashValueTable:
    def __init__(self, entries):
        self._entries = entries

    def lookup(self, period, payment, gender, age, policy_year):
        return self._entries.get((period, payment, gender, age, policy_year))


def parse_cash_grid_layout(layout):
    entries = {}
    for table in layout.get('tables', []):
        last = [''] * 6
        for row in table.get('grid') or []:
            cells = [_cell_text(cell) for cell in row]
            if not cells or cells[0] == '保险期间':
                continue
            for i in range(min(6, len(cells))):
                if cells[i]:
                    last[i] = cells[i]
                else:
                    cells[i] = last[i]
            if len(cells) < 6:
                continue
            period, payment, gender, age_text, year_text, value_text = cells[:6]
            if not (age_text.isdigit() and year_text.isdigit()):
                continue
            value = _parse_number(value_text)
            if period and payment and gender and value is not None:
                entries[(period, payment, gender, int(age_text), int(year_text))] = value
    return CashValueTable(entries)


def parse_cash_text_lines(lines):
    entries = {}
    for line in lines:
        match = _CASH_LINE_PATTERN.match(line.strip())
        if not match:
            continue
        period, payment, gender, age, year, value = match.groups()
        entries[(period, payment, gender, int(age), int(year))] = float(value.replace(',', ''))
    return CashValueTable(entries)


def parse_cash_full_text_pdf(pdf_bytes):
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    lines = []
    for page in reader.pages:
        text = page.extract_text() or ''
        lines.extend(text.splitlines())
    return parse_cash_text_lines(lines)


def death_ratio(reached_age):
    if reached_age <= 17:
        return 1.0
    if reached_age <= 40:
        return 1.6
    if reached_age <= 60:
        return 1.4
    return 1.2


def build_endowment_illustration(rate_table, cash_table, *, age, gender, coverage, payment, annual_premium):
    period_label = COVERAGE_LABELS[coverage]
    gender_label = GENDER_LABELS[gender]
    payment_label = PAYMENT_LABELS[payment]
    pay_years = PAYMENT_YEARS[payment]
    coverage_age = COVERAGE_AGES[coverage]
    total_years = coverage_age - age
    if total_years < 1:
        raise ParameterOutOfRangeError(f'投保年龄 {age} 超出{period_label}的可保范围')

    rate_unit = rate_table.lookup(period_label, gender_label, payment_label, age)
    if rate_unit is None:
        raise ParameterOutOfRangeError(f'费率表中没有 {period_label}/{gender_label}/{payment_label}/{age}岁 对应的数值')
    sum_assured = rate_unit * annual_premium / 1000.0

    rows = []
    for year in range(1, total_years + 1):
        annual = annual_premium if year <= pay_years else 0.0
        cumulative = min(year, pay_years) * annual_premium
        cash_unit = cash_table.lookup(period_label, payment_label, gender_label, age, year)
        if cash_unit is None:
            raise ParameterOutOfRangeError(f'现金价值表中没有 {period_label}/{payment_label}/{gender_label}/{age}岁/第{year}年度 对应的数值')
        cash_value = cash_unit * annual_premium / 1000.0
        maturity = sum_assured if year == total_years else 0.0
        death = max(cash_value, cumulative * death_ratio(age + year - 1))
        rows.append({
            'policyYear': year,
            'age': age + year,
            'annualPremium': _round_yuan(annual),
            'cumulativePremium': _round_yuan(cumulative),
            'maturityBenefit': _round_yuan(maturity),
            'deathBenefit': _round_yuan(death),
            'cashValue': _round_yuan(cash_value),
        })
    return {'sumAssured': _round_yuan(sum_assured), 'rows': rows}


class BenefitIllustrationService:
    def __init__(self, document_service):
        self.documents = document_service
        self._cache = {}

    async def _product_name(self, product_id):
        rows = await asyncio.to_thread(self.documents._query, self.documents.product, 'SELECT name FROM product WHERE id=%s', (product_id,))
        if not rows:
            raise ProductNotFoundError(product_id)
        return rows[0]['name']

    async def _rate_table(self, document_row):
        key = ('rate', document_row['id'], document_row['sha256'])
        if key not in self._cache:
            body = await self.documents.get_saved_layout(document_row['id'])
            self._cache[key] = parse_rate_layout(json.loads(body))
        return self._cache[key]

    async def _cash_table(self, document_row):
        key = ('cash', document_row['id'], document_row['sha256'])
        if key not in self._cache:
            body = await self.documents.get_saved_layout(document_row['id'])
            layout = json.loads(body)
            if layout.get('tables'):
                table = parse_cash_grid_layout(layout)
            else:
                source = await self.documents.get_file(document_row['id'])
                table = await asyncio.to_thread(parse_cash_full_text_pdf, source['body'])
            self._cache[key] = table
        return self._cache[key]

    async def generate(self, *, product_id, age, gender, coverage, payment, annual_premium):
        name = await self._product_name(product_id)
        if '两全' not in name or '分红' in name:
            raise UnsupportedProductTemplateError(f'暂不支持产品「{name}」的演示模板，当前仅支持传统两全保险（非分红）')

        documents = await self.documents._current_layout_documents(product_id)
        by_type = {row['type']: row for row in documents}
        for required in ('rate_table', 'cash_value_table'):
            if required not in by_type:
                raise BenefitDataMissingError(f'产品缺少{required}资料，无法生成利益演示表')

        try:
            rate_table = await self._rate_table(by_type['rate_table'])
        except SavedLayoutNotFoundError:
            raise BenefitDataMissingError('费率表尚未解析，请先在产品详情中执行资料解析')
        try:
            cash_table = await self._cash_table(by_type['cash_value_table'])
        except SavedLayoutNotFoundError:
            raise BenefitDataMissingError('现金价值表尚未解析，请先在产品详情中执行资料解析')

        result = build_endowment_illustration(
            rate_table, cash_table,
            age=age, gender=gender, coverage=coverage, payment=payment, annual_premium=annual_premium,
        )
        return {
            'productId': product_id,
            'productName': name,
            'template': 'traditional_endowment',
            'coveragePeriod': coverage,
            'coveragePeriodLabel': COVERAGE_LABELS[coverage],
            'paymentPeriod': payment,
            'paymentPeriodLabel': PAYMENT_LABELS[payment],
            'sumAssured': result['sumAssured'],
            'rows': result['rows'],
            'notes': ['数值按利益演示惯例取整，与实际数值可能略有差异', '身故保险金取现金价值与已交保费×到达年龄比例系数的较大值'],
        }
