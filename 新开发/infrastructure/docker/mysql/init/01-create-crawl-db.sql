-- 产品库（baoxian_product）由 MYSQL_DATABASE 与 entrypoint 创建；
-- 应用账号（baoxian）也由 entrypoint 按 MYSQL_USER / MYSQL_PASSWORD 创建。
-- 同时创建采集运行库和两套隔离集成测试库。
CREATE DATABASE IF NOT EXISTS `baoxian_crawl`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

GRANT ALL PRIVILEGES ON `baoxian_crawl`.* TO 'baoxian'@'%';

CREATE DATABASE `baoxian_product_test` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE `baoxian_crawl_test` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
GRANT ALL PRIVILEGES ON `baoxian_product_test`.* TO 'baoxian'@'%';
GRANT ALL PRIVILEGES ON `baoxian_crawl_test`.* TO 'baoxian'@'%';
