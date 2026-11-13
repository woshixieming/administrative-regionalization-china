# PostgreSQL 版安装与迁移

## 1. 环境与项目内容

- Python 3.10+；项目新增 `psycopg[binary]` 驱动和 `psycopg-pool` 连接池。
- PostgreSQL 14+，安装包需包含 `pg_trgm` 扩展。导入账号需拥有目标数据库中的建表和安装扩展权限。
- Node.js 满足 `web/package.json` 中的 engines（20.19+ 的 20.x、22.13+ 的 22.x 或 24+），使用 npm。
- 首次导入需要 `database/data.sql`。它包含建表、索引、全国数据、版本元数据、变化报告和公开修订台账，可独立导入空数据库，不需要原机器的 CSV / JSON 文件。

如果通过 Git 获取源码，请另外复制 `database/data.sql`，或在有完整 `web/public/data/` 的机器执行：

```bash
python3 -m backend.manage export-sql
```

SQL 是生成产物，已被 Git 忽略；`database/schema.sql` 和生成代码纳入 Git。SQL 使用 `COPY FROM STDIN`，可以通过下面的 Python 命令导入，也可以使用 `psql -X -v ON_ERROR_STOP=1 -d china_divisions -f database/data.sql`。GUI 导入工具必须支持 psql COPY 数据段，不能简单拆分 SQL 语句执行。

## 2. 安装依赖和配置

以下命令在项目根目录执行（macOS / Linux）：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
cp config/backend.example.ini config/backend.ini
```

Windows PowerShell 使用 `py -3 -m venv .venv`，随后运行 `.\.venv\Scripts\Activate.ps1` 和 `Copy-Item config/backend.example.ini config/backend.ini`；其余 `python -m ...` 命令相同。数据抓取和刷新仍使用 Bash、curl、rsync，Windows 执行这部分请使用 WSL。

填写 `config/backend.ini`：

```ini
[server]
host = 127.0.0.1
port = 3100
static_dir = web/out
max_requests = 32

[postgres]
host = 127.0.0.1
port = 5432
dbname = china_divisions
user = divisions_app
password =
sslmode = prefer
connect_timeout = 5
statement_timeout_ms = 30000
pool_min = 1
pool_max = 8
```

- `password` 填自己的数据库密码，支持 `%` 等字符，不做字符串插值；不要把真实配置提交 Git。
- `static_dir` 相对项目根目录；命令的 `--config` 可以指定其他配置路径。
- 远程 PostgreSQL 按数据库实际 TLS 要求设置 `sslmode`，例如 `verify-full`，证书可使用 libpq 标准配置。
- `statement_timeout_ms` 限制查询耗时；批量导入使用独立连接，关闭此查询限制。
- 对外提供服务时设置后端绑定地址，通常将后端保持在回环地址，由已有 Nginx / HTTPS 入口代理所有请求到后端。不要沿用仅提供静态文件的旧 Nginx 模板。

## 3. 创建数据库与导入

先用 PostgreSQL 管理员账号连接，在 psql 中创建角色并交互设置密码：

```sql
CREATE ROLE divisions_owner LOGIN;
\password divisions_owner
CREATE DATABASE china_divisions OWNER divisions_owner;
```

将配置中的 `user` 和 `password` 先填为这个导入账号，然后执行：

```bash
python -m backend.manage import-sql
```

这个命令直接读取 INI 并流式导入，不需要安装 psql 命令行客户端。它只支持本项目 `export-sql` 生成的 SQL。
不会创建数据库或角色，不会修改其他 schema；数据库表集中在 `divisions` schema。

导入在事务内完成：校验父关系和记录数量后切换当前版本。导入失败不会发布半套数据；相同版本可重复导入，版本号相同但内容校验和不同会拒绝覆盖。导入结束后执行 ANALYZE 更新查询统计。

可以为运行服务单独建立只读账号。在管理员 psql 中执行：

```sql
CREATE ROLE divisions_app LOGIN;
\password divisions_app
\connect china_divisions
\i database/permissions.sql
```

然后将 `config/backend.ini` 改为只读账号。把导入账号另存到一个权限受限且在项目外的 INI 文件，后续导入使用 `--config /绝对路径/import.ini`。配置格式相同。不要给浏览器数据库连接凭据。

## 4. 构建和启动

保持 Python 虚拟环境已激活：

```bash
cd web
npm ci
npm run build
cd ..
python -m backend.server
```

打开 <http://127.0.0.1:3100>。生产构建不连接数据库；网站运行时必须启动 Python 后端并能连接已导入数据的 PostgreSQL。
`web/` 中的 `npm start` 也会调用后端，在 macOS / Linux 上使用已激活虚拟环境中的 `python3`。

检查服务：

```bash
curl http://127.0.0.1:3100/api/health
```

返回 `status: ok` 和当前 `datasetVersion` 表示数据库和版本入口可用。API 错误以非 200 状态和 `error` 返回，避免把数据库故障当成空列表。服务只开放只读查询，不提供匿名数据写入接口。

开发前端时先保持后端运行，再开另一个终端：

```bash
cd web
npm run dev
```

开发页面在 <http://127.0.0.1:3101>，`/api/` 默认代理到 `http://127.0.0.1:3100`。后端改端口后，可通过启动环境变量 `DIVISIONS_API_ORIGIN` 指定代理目标，例如 `DIVISIONS_API_ORIGIN=http://127.0.0.1:3200 npm run dev`。生产环境不使用此开发代理。

## 5. API 与数据模型

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 检查数据库和当前版本 |
| `GET /api/data/meta.json` | 当前版本元数据 |
| `GET /api/data/v/{version}/provinces.json` | 指定版本省列表 |
| `GET /api/data/v/{version}/l2/{provinceCode}.json` | 地级列表，省直辖容器另在 meta.directCities 中 |
| `GET /api/data/v/{version}/l3/{cityCode}.json` | 县级列表 |
| `GET /api/data/v/{version}/l4/{前四位代码}.json` | 按县码分组的乡级列表 |
| `GET /api/data/v/{version}/l5/{前四位代码}.json` | 按乡码分组的村级列表 |
| `GET /api/search?version=...&q=东城&province=&level=0&history=1` | SQL 搜索，返回前 200 条、真实总数和完整路径 |
| `GET /api/export?version=...&level=3&code=110101000000&history=1` | 区域及下级、必要祖先；前端生成现有 JSON / CSV 格式 |
| `GET /api/status` | 当前导入版本附带的公开任务状态 |
| `GET /api/data/v/{version}/changes/summary.json` | 版本差异摘要 |

路径带 `.json` 是为保留前端数据契约，响应来自 PostgreSQL 查询，不是读取 JSON 文件。
搜索使用参数化查询、代码前缀索引和名称 trigram 索引；两字名称子串可能无法充分利用 trigram 索引，查询超时可通过省份和层级筛选缩小范围。
浏览与导出固定到同一数据版本；历史筛选会排除历史父级下的村级后代。

- `datasets`：不可变版本、校验和、完整元数据。
- `regions`：五级记录，主键为 `(version, level, code)`，父级外键包括层级，兼容同码虚拟容器。
- `documents`：版本附带的报告、修订台账、清单及公开状态。
- `current_dataset`：事务内切换的当前版本指针。

原始字段保存在 `payload jsonb`；代码、名称、父级、祖先路径和继承历史标记独立建列和索引。
来源截止日期继续使用原数据口径，数据库迁移不会更新村级截止日期。

## 6. 更新数据和迁移电脑

数据库版更新入口（使用导入账号配置）：

```bash
# 抓取、数据加工、校验、生成 SQL、事务导入；需要原始数据和刷新工具
python -m backend.manage --config /绝对路径/import.ini refresh

# 不重新抓取，用已有 data/raw 和 data/cache 重建后导入
python -m backend.manage --config /绝对路径/import.ini refresh --skip-fetch
```

数据库导入失败时网站继续读原数据库版本，文件加工结果可能已经更新；修复问题后可再次 `export-sql` / `import-sql`。状态面板显示的是导入时附带的任务状态，不实时展示尚未导入数据库的抓取进度。
新版本导入不删除旧数据库版本，已打开页面可以继续浏览和导出。数据库历史版本的保留与文件快照独立管理，本次不自动删除数据库历史。

旧 `scripts/schedule.py` 安装的 cron 仅调用文件刷新脚本，**不会发布数据库版本**；有旧调度任务时先查看并移除本项目旧任务，再用操作系统调度以上 `backend.manage refresh` 命令。不要同时运行两套调度。

迁移电脑需携带：

1. 源码及 `web/package-lock.json`。
2. `database/data.sql`，或使用 PostgreSQL 标准备份恢复实际数据库。
3. 示例配置，在新电脑填写新的数据库连接参数。
4. 如需继续抓取和重建，再带上 `data/raw/`、`data/cache/` 和 `data/patches/`（存在时）。

只运行网站不需要 `web/public/data/`、旧快照、抓取缓存或原电脑的 `node_modules/`。
复制后按上面的安装、导入和构建步骤启动即可。完整打包中的 SQL 为打包时版本，之后请重新导出或备份数据库。

## 7. 验证

```bash
python -B -m unittest discover -s tests -v
cd web
npm run check
```

后端集成测试必须显式指定专用测试库配置：`DIVISIONS_TEST_CONFIG=/绝对路径/test.ini python -B -m unittest discover -s tests -p test_backend.py -v`。
测试库名必须以 `_test` 结尾，且已导入本项目 SQL；集成测试会重复导入数据并验证冲突回滚，请勿指向生产数据库。

当前未替换原服务器的 Nginx、systemd 或 CI 配置。上线时用服务管理器托管 `python -m backend.server --config ...`，由反向代理提供 HTTPS；迁移前先在目标环境完成健康检查和页面验收。
