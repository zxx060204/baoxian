def qualified_database(source, target):
    left, right = source._connection_options, target._connection_options
    if any(left[key] != right[key] for key in ('host', 'port', 'user', 'password')):
        raise ValueError('产品库和运行库必须使用同一 MySQL 实例及用户')
    name = right['database']
    if not name:
        raise ValueError('数据库名称不能为空')
    return '`' + name.replace('`', '``') + '`'
