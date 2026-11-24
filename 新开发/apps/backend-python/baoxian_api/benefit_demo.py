import argparse
import asyncio
import os
import sys
from pathlib import Path

from .benefit_illustration import (
    BenefitDataMissingError,
    BenefitIllustrationService,
    ParameterOutOfRangeError,
    UnsupportedProductTemplateError,
)
from .documents import DocumentService
from .products import ProductNotFoundError
from .storage import MinioStorage

GENDERS = {'male': 'male', 'female': 'female', 'm': 'male', 'f': 'female', '男': 'male', '女': 'female'}
COVERAGES = {**{f'to_{n}': f'to_{n}' for n in (60, 70, 80, 90)}, **{str(n): f'to_{n}' for n in (60, 70, 80, 90)}}
PAYMENTS = {'single': 'single', '0': 'single', '1': 'single', '趸': 'single', '趸交': 'single', '3': '3', '5': '5', '10': '10', '3年': '3', '5年': '5', '10年': '10'}


def _load_dotenv():
    env_path = Path(__file__).resolve().parents[3] / '.env'
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        os.environ.setdefault(key.strip(), value.strip())


def _build_service():
    _load_dotenv()
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if not database_url:
        sys.exit('缺少 DATABASE_URL（请确认 新开发/.env 存在）')
    crawl_url = os.environ.get('CRAWL_DATABASE_URL', '').strip() or database_url
    storage = MinioStorage(
        os.environ.get('MINIO_ENDPOINT', '127.0.0.1'),
        int(os.environ.get('MINIO_PORT', '9000')),
        os.environ.get('MINIO_USE_SSL', 'false') == 'true',
        os.environ.get('MINIO_ACCESS_KEY', 'baoxian'),
        os.environ.get('MINIO_SECRET_KEY', 'baoxian_minio_dev'),
        os.environ.get('MINIO_BUCKET', 'baoxian-documents'),
    )
    return BenefitIllustrationService(DocumentService(database_url, crawl_url, storage))


def _fmt(value):
    return f'{value:,}'


def main():
    parser = argparse.ArgumentParser(description='利益演示表生成测试程序（传统两全保险）')
    parser.add_argument('--product', type=int, required=True, help='产品 ID，如 13（A05）')
    parser.add_argument('--age', type=int, required=True, help='投保年龄，如 40')
    parser.add_argument('--gender', required=True, help='性别：male/female 或 男/女')
    parser.add_argument('--coverage', required=True, help='保险期间：60/70/80/90（保至 X 岁）')
    parser.add_argument('--payment', required=True, help='交费期间：single(趸交)/3/5/10')
    parser.add_argument('--premium', type=float, required=True, help='年交保费（趸交为一次交保费）')
    parser.add_argument('--csv', default=None, help='可选：把演示表另存为 CSV 文件路径')
    args = parser.parse_args()

    gender = GENDERS.get(args.gender.lower() if args.gender.isascii() else args.gender)
    coverage = COVERAGES.get(args.coverage.lower())
    payment = PAYMENTS.get(args.payment)
    if gender is None or coverage is None or payment is None:
        sys.exit('参数无效：--gender 用 male/female/男/女；--coverage 用 60/70/80/90；--payment 用 single/3/5/10')

    service = _build_service()
    try:
        result = asyncio.run(service.generate(
            product_id=args.product, age=args.age, gender=gender,
            coverage=coverage, payment=payment, annual_premium=args.premium,
        ))
    except ProductNotFoundError:
        sys.exit(f'产品 {args.product} 不存在')
    except UnsupportedProductTemplateError as error:
        sys.exit(str(error))
    except BenefitDataMissingError as error:
        sys.exit(str(error))
    except ParameterOutOfRangeError as error:
        sys.exit(f'参数超出表格范围：{error}')

    print(f"\n产品：{result['productName']}（ID {result['productId']}）")
    premium_text = f'{args.premium:,.0f}' if args.premium == int(args.premium) else f'{args.premium:,.2f}'
    print(f"投保：{args.age} 岁 {result['paymentPeriodLabel']} 年交保费 {premium_text} 元 {result['coveragePeriodLabel']}")
    print(f"基本保额：{_fmt(result['sumAssured'])} 元\n")
    header = ('保单年度', '年末年龄', '当年保费', '累计保费', '满期金', '身故保险金', '退保金(现金价值)')
    widths = (8, 8, 12, 12, 12, 12, 16)
    print(''.join(name.rjust(width) for name, width in zip(header, widths)))
    for row in result['rows']:
        values = (row['policyYear'], row['age'], row['annualPremium'], row['cumulativePremium'],
                  row['maturityBenefit'], row['deathBenefit'], row['cashValue'])
        print(''.join((_fmt(v) if isinstance(v, (int, float)) else str(v)).rjust(width) for v, width in zip(values, widths)))
    print('\n注：数值按演示惯例取整，可能与实际略有差异。')

    if args.csv:
        import csv
        with open(args.csv, 'w', newline='', encoding='utf-8-sig') as file:
            writer = csv.writer(file)
            writer.writerow(header)
            for row in result['rows']:
                writer.writerow([row['policyYear'], row['age'], row['annualPremium'], row['cumulativePremium'],
                                 row['maturityBenefit'], row['deathBenefit'], row['cashValue']])
        print(f'已导出：{args.csv}')


if __name__ == '__main__':
    main()
