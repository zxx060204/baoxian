-- 由“产品身份与协会记录归组”变更引入。
-- 在产品库执行一次；本次重置会随后清空现有 company/product/document 数据。
ALTER TABLE product
  ADD COLUMN identity_group VARCHAR(16) NOT NULL DEFAULT 'individual' AFTER normalized_name,
  ADD COLUMN same_name_count INT UNSIGNED NOT NULL DEFAULT 1 AFTER identity_group;

CREATE TABLE product_index_record (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  company_id BIGINT UNSIGNED NOT NULL,
  product_id BIGINT UNSIGNED NOT NULL,
  source_key VARCHAR(160) NOT NULL,
  association_record_id VARCHAR(128) DEFAULT NULL,
  name VARCHAR(255) NOT NULL,
  normalized_name VARCHAR(255) NOT NULL,
  sales_status VARCHAR(16) NOT NULL,
  sales_status_raw VARCHAR(64) DEFAULT NULL,
  sale_date_raw VARCHAR(255) DEFAULT NULL,
  status_fetched_at DATETIME NOT NULL,
  source_url VARCHAR(1024) NOT NULL,
  category_code VARCHAR(64) NOT NULL,
  category_name VARCHAR(255) NOT NULL,
  official_product_code VARCHAR(128) DEFAULT NULL,
  filing_number VARCHAR(128) DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY product_index_company_source_unique (company_id, source_key),
  KEY product_index_product_idx (product_id),
  KEY product_index_name_idx (company_id, normalized_name),
  CONSTRAINT product_index_company_fk FOREIGN KEY (company_id) REFERENCES company (id),
  CONSTRAINT product_index_product_fk FOREIGN KEY (product_id) REFERENCES product (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
