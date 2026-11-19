# Docker 开发环境（MySQL / Redis / MinIO）

> 状态：现行
> 适用日期：2026-11-19

本目录是 P1/P2 首轮开发的本地依赖栈。MySQL、Redis、MinIO 全部在 Docker 中运行，**不使用本机 MySQL 服务**，也不迁移旧数据。

## 启动

1. 启动 Docker Desktop（Linux 容器模式）。
2. 进入本目录并准备环境变量：

   ```powershell
   cd 新开发/infrastructure/docker
   Copy-Item .env.example .env
   ```

3. 启动并确认状态：

   ```powershell
   docker compose up -d
   docker compose ps
   ```

   MySQL 首次启动会执行 `mysql/init/` 下的初始化 SQL，为 `baoxian_product`、`baoxian_crawl` 和对应的两个 `_test` 库建立现行表结构。应用账号可访问这四个库。

4. 查看初始化日志：

   ```powershell
   docker compose logs -f mysql
   ```

## 连接信息

| 服务 | 地址 | 凭据 |
| --- | --- | --- |
| MySQL | `127.0.0.1:3307` | 用户 `baoxian`，密码见 `.env` 的 `MYSQL_PASSWORD`；root 密码见 `MYSQL_ROOT_PASSWORD`；库 `baoxian_product`、`baoxian_crawl` |
| Redis | `127.0.0.1:6379` | 密码见 `.env` 的 `REDIS_PASSWORD` |
| MinIO API | `http://127.0.0.1:9000` | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` |
| MinIO 控制台 | `http://127.0.0.1:9001` | 同上 |

连接串示例（开发默认值，实际以 `.env` 为准）：

```text
mysql://baoxian:baoxian_dev@127.0.0.1:3307/baoxian_product
mysql://baoxian:baoxian_dev@127.0.0.1:3307/baoxian_crawl
redis://:baoxian_redis_dev@127.0.0.1:6379/0
http://baoxian:baoxian_minio_dev@127.0.0.1:9000
```

## 数据与重置

- 数据保存在命名卷 `mysql-data`、`redis-data`、`minio-data`。
- `docker compose down` 停止并保留数据；`docker compose down -v` 删除数据卷（含两个库、队列数据与对象存储），仅在明确要重建环境时使用。
- 初始化 SQL 只在 `mysql-data` 为空时执行；`mysql/init/schema/` 保存当前结构基线，已有库的升级按单独的迁移步骤执行。

## 说明

- 本机 MySQL 仍在运行，但**不参与本项目**；容器 MySQL 映射到 3307，避免与 3306 冲突。
- MinIO 官方镜像源（Docker Hub `minio/minio`、Quay）已不再允许匿名拉取；当前使用社区重建镜像 `pgsty/minio`，入口参数与官方镜像一致。上线前应锁定镜像 digest 或改用自建镜像，不能长期依赖 `latest`。
- `.env` 不提交仓库，`.env.example` 只含开发默认值。
