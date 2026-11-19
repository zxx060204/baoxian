-- Current empty database schema. Applied only on first MySQL initialization.
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE TABLE `app_setting` (
  `id` int NOT NULL,
  `global_concurrency` int NOT NULL DEFAULT '2',
  `domain_interval_ms` int NOT NULL DEFAULT '1000',
  `request_timeout_ms` int NOT NULL DEFAULT '30000',
  `retry_attempts` int NOT NULL DEFAULT '3',
  `association_crawl_enabled` tinyint(1) NOT NULL DEFAULT '1',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `index_refresh_minutes` int NOT NULL DEFAULT '10080',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `association_index_progress` (
  `category_code` varchar(64) NOT NULL,
  `category_name` varchar(255) NOT NULL,
  `next_page` int NOT NULL DEFAULT '1',
  `cursor` varchar(255) DEFAULT NULL,
  `cycle` int NOT NULL DEFAULT '1',
  `completed_at` datetime DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`category_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `crawl_schedule` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `category_code` varchar(64) NOT NULL,
  `category_name` varchar(255) NOT NULL,
  `every_minutes` int NOT NULL,
  `enabled` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `crawl_schedule_category_unique` (`category_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `crawl_task` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `type` varchar(32) NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'queued',
  `scope` json NOT NULL,
  `parent_task_id` bigint unsigned DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `started_at` datetime DEFAULT NULL,
  `finished_at` datetime DEFAULT NULL,
  `config` json DEFAULT NULL,
  `reported_total` int DEFAULT NULL,
  `read_count` int NOT NULL DEFAULT '0',
  `unique_count` int NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  KEY `crawl_task_status_idx` (`status`),
  KEY `crawl_task_type_idx` (`type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `crawl_task_product` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `task_id` bigint unsigned NOT NULL,
  `product_id` bigint unsigned NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `crawl_task_product_unique` (`task_id`,`product_id`),
  KEY `crawl_task_product_product_idx` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `crawl_task_unit` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `task_id` bigint unsigned NOT NULL,
  `unit_key` varchar(255) NOT NULL,
  `page` int NOT NULL,
  `state` varchar(32) NOT NULL DEFAULT 'pending',
  `cursor` varchar(255) DEFAULT NULL,
  `attempts` int NOT NULL DEFAULT '0',
  `last_error` text,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `crawl_task_unit_task_key_unique` (`task_id`,`unit_key`),
  KEY `crawl_task_unit_state_idx` (`state`),
  CONSTRAINT `crawl_task_unit_task_id_crawl_task_id_fk` FOREIGN KEY (`task_id`) REFERENCES `crawl_task` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `document_failure` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `task_id` bigint unsigned DEFAULT NULL,
  `company_id` bigint unsigned DEFAULT NULL,
  `product_id` bigint unsigned DEFAULT NULL,
  `type` varchar(32) NOT NULL,
  `title` varchar(512) NOT NULL,
  `download_url` varchar(1024) NOT NULL,
  `source_page_url` varchar(1024) NOT NULL,
  `reason` varchar(32) NOT NULL,
  `message` text NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `document_failure_product_idx` (`product_id`),
  KEY `document_failure_task_idx` (`task_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `document_snapshot` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `task_id` bigint unsigned DEFAULT NULL,
  `document_id` bigint unsigned NOT NULL,
  `product_ids` json NOT NULL,
  `source_url` varchar(1024) NOT NULL,
  `download_url` varchar(1024) NOT NULL,
  `snapshot_key` varchar(512) NOT NULL,
  `page_title` varchar(512) DEFAULT NULL,
  `supporting_text` text,
  `fetched_at` datetime NOT NULL,
  `snapshot_kind` varchar(32) NOT NULL DEFAULT 'unknown',
  PRIMARY KEY (`id`),
  KEY `document_snapshot_document_idx` (`document_id`),
  KEY `document_snapshot_task_idx` (`task_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `product_document_status` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `product_id` bigint unsigned NOT NULL,
  `type` varchar(32) NOT NULL,
  `status` varchar(32) NOT NULL,
  `reason` varchar(512) DEFAULT NULL,
  `task_id` bigint unsigned DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `product_document_status_unique` (`product_id`,`type`),
  KEY `product_document_status_product_idx` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `review_item` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `type` varchar(32) NOT NULL,
  `status` varchar(16) NOT NULL DEFAULT 'pending',
  `task_id` bigint unsigned DEFAULT NULL,
  `payload` json NOT NULL,
  `resolution` varchar(16) DEFAULT NULL,
  `resolved_product_id` bigint unsigned DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `resolved_at` datetime DEFAULT NULL,
  `resolved_by` varchar(64) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `review_item_status_idx` (`status`),
  KEY `review_item_type_idx` (`type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

SET FOREIGN_KEY_CHECKS = 1;
