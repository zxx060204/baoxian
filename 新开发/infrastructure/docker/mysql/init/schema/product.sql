-- Current empty database schema. Applied only on first MySQL initialization.
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE TABLE `company` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL,
  `official_site_url` varchar(1024) DEFAULT NULL,
  `notes` text,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `document_entry_url` varchar(1024) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `company_name_unique` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `document` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `sha256` varchar(64) NOT NULL,
  `filename` varchar(512) DEFAULT NULL,
  `mime_type` varchar(128) NOT NULL,
  `size` int NOT NULL,
  `storage_key` varchar(512) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `source_url` varchar(1024) DEFAULT NULL,
  `download_url` varchar(1024) DEFAULT NULL,
  `page_title` varchar(512) DEFAULT NULL,
  `product_id` bigint unsigned DEFAULT NULL,
  `type` varchar(32) DEFAULT NULL,
  `link_status` varchar(16) NOT NULL DEFAULT 'current',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `document_product_type_hash_unique` (`product_id`,`type`,`sha256`),
  CONSTRAINT `document_product_id_product_id_fk` FOREIGN KEY (`product_id`) REFERENCES `product` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `product` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `company_id` bigint unsigned NOT NULL,
  `name` varchar(255) NOT NULL,
  `normalized_name` varchar(255) NOT NULL,
  `identity_group` varchar(16) NOT NULL DEFAULT 'individual',
  `same_name_count` int unsigned NOT NULL DEFAULT '1',
  `sales_status` varchar(16) NOT NULL,
  `sales_status_raw` varchar(64) DEFAULT NULL,
  `status_fetched_at` datetime NOT NULL,
  `source_url` varchar(1024) NOT NULL,
  `category_code` varchar(64) NOT NULL,
  `category_name` varchar(255) NOT NULL,
  `association_record_id` varchar(128) DEFAULT NULL,
  `official_product_code` varchar(128) DEFAULT NULL,
  `filing_number` varchar(128) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `official_product_url` varchar(1024) DEFAULT NULL,
  `confirmed_name` varchar(255) DEFAULT NULL,
  `confirmed_sales_status` varchar(16) DEFAULT NULL,
  `correction_note` text,
  `confirmed_at` datetime DEFAULT NULL,
  `confirmed_by` varchar(64) DEFAULT NULL,
  `previous_sales_status_raw` varchar(64) DEFAULT NULL,
  `integration_status` varchar(32) NOT NULL DEFAULT 'not_started',
  `integration_status_reason` text,
  `integration_last_task_id` bigint unsigned DEFAULT NULL,
  `integration_status_updated_at` datetime DEFAULT NULL,
  `sale_date_raw` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `product_company_name_idx` (`company_id`,`normalized_name`),
  KEY `product_sales_status_idx` (`sales_status`),
  KEY `product_company_record_idx` (`company_id`,`association_record_id`),
  CONSTRAINT `product_company_id_company_id_fk` FOREIGN KEY (`company_id`) REFERENCES `company` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `product_index_record` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `company_id` bigint unsigned NOT NULL,
  `product_id` bigint unsigned NOT NULL,
  `source_key` varchar(160) NOT NULL,
  `association_record_id` varchar(128) DEFAULT NULL,
  `name` varchar(255) NOT NULL,
  `normalized_name` varchar(255) NOT NULL,
  `sales_status` varchar(16) NOT NULL,
  `sales_status_raw` varchar(64) DEFAULT NULL,
  `sale_date_raw` varchar(255) DEFAULT NULL,
  `status_fetched_at` datetime NOT NULL,
  `source_url` varchar(1024) NOT NULL,
  `category_code` varchar(64) NOT NULL,
  `category_name` varchar(255) NOT NULL,
  `official_product_code` varchar(128) DEFAULT NULL,
  `filing_number` varchar(128) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `product_index_company_source_unique` (`company_id`,`source_key`),
  KEY `product_index_product_idx` (`product_id`),
  KEY `product_index_name_idx` (`company_id`,`normalized_name`),
  CONSTRAINT `product_index_company_fk` FOREIGN KEY (`company_id`) REFERENCES `company` (`id`),
  CONSTRAINT `product_index_product_fk` FOREIGN KEY (`product_id`) REFERENCES `product` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

SET FOREIGN_KEY_CHECKS = 1;
