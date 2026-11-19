USE baoxian_product;
SOURCE /docker-entrypoint-initdb.d/schema/product.sql;

USE baoxian_crawl;
SOURCE /docker-entrypoint-initdb.d/schema/crawl.sql;

USE baoxian_product_test;
SOURCE /docker-entrypoint-initdb.d/schema/product.sql;

USE baoxian_crawl_test;
SOURCE /docker-entrypoint-initdb.d/schema/crawl.sql;
