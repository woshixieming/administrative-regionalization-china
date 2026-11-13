# 中国行政区划五级数据 · PostgreSQL 版

提供省、地、县、乡、村五级行政区划的浏览、搜索、代码定位、分享和区域导出。
采用 **Next.js / React 前端 + Python 只读 API 后端 + PostgreSQL 数据库**，数据遵循“前四级官方来源 + 村级历史冻结基线”的 4 + 1 口径。

## 架构

```text
浏览器
  ├── 页面与前端资源 ──→ Python 后端提供 web/out 构建产物
  └── /api/ 查询 ─────→ Python 后端 ──→ PostgreSQL

数据维护
  原始四级数据 + 村级 CSV + 已审核修订
    → 合并与校验 → 生成 SQL → 事务导入 PostgreSQL → 切换当前数据版本
```

- **前端**：Next.js / React，负责五级联动、查询筛选、路径恢复和 JSON / CSV 下载。
- **后端**：Python，统一提供页面资源和只读 API，使用 `psycopg` 与连接池访问 PostgreSQL。
- **数据库**：保存五级区划、父级关系、数据版本、来源标记、变化报告和公开修订台账。
- **数据维护**：Python / Bash 管线负责抓取、构建、校验和数据库发布；浏览器不连接数据库，也不直接读取数据目录中的 JSON 文件。

生产环境只需运行 Python 后端和 PostgreSQL；Node.js / npm 用于安装前端依赖、开发和构建。
详细参数、账号权限及迁移步骤见 [PostgreSQL 使用说明](docs/postgresql.md)。

## 功能

- **五级浏览与代码定位**：逐级查看行政区划，支持输入 6、9 或 12 位代码定位。
- **全国搜索**：按名称子串或代码前缀查询，可限定省份与层级；返回真实匹配总数，并按代码、层级排序展示前 200 条，每页 20 条。
- **历史筛选**：可隐藏历史补层及其下挂记录；当前选择落在历史路径时退回可见上级。
- **复制与分享**：复制地址、分级代码和带筛选条件的链接，支持刷新恢复、浏览器前进和后退；自动复制不可用时提供手动复制内容。
- **区域导出**：导出所选区域及全部下级为 JSON / CSV，遵循历史筛选，保留完整路径、来源、截止日期和状态；CSV 包含 UTF-8 BOM、引号转义及公式前缀防护。
- **版本与修订**：浏览、查询和导出绑定同一版本；页面展示版本差异、数据来源、历史补层、虚拟容器和人工修订记录。

## 数据口径

当前配套数据版本为 `7449cc56e4935cfafb146f72`，生成于 `2026-10-20 16:58:01`。下表统计不含历史补层和虚拟容器的建制记录；运行中的实际版本以页面和 `/api/data/meta.json` 为准。

| 层级 | 来源 | 截止日期 | 条数 |
| --- | --- | --- | --- |
| 省级 | 民政部 · 国家地名信息库 | 2025-12-31 | 34 |
| 地级 | 民政部 · 国家地名信息库 | 2025-12-31 | 333 |
| 县级 | 民政部 · 国家地名信息库 | 2025-12-31 | 2,847 |
| 乡级 | 民政部 · 国家地名信息库 | 2025-12-31 | 39,006 |
| 村级 | 国家统计局历史快照 | 2023-06-30 | 620,572 |

数据另包含 8 个地级、4 个县级虚拟容器，以及 135 个县级、7,203 个乡级历史补层，共 670,142 条展示记录。

- **村级冻结基线**：采用 2023 年快照，日期不会随抓取或数据库导入自动更新。基线版本、精确行数和 SHA-256 由 `scripts/baseline.json` 固定并校验。
- **历史补层**：当村级记录的父乡级或县级在较新的四级数据中缺失时，从历史快照补回父层并标记 `legacy`，仅用于挂接数据，不代表当前建制。
- **虚拟容器**：直辖市、省直辖县级行政区和不设县级的直管市使用显示容器保持五级链路，标记 `virtual`，不计入真实建制数量。
- **来源与日期**：保留来源截止日期、抓取失败沿用标记及人工修订依据；首次抓取没有已确认日期时，需要显式提供 `MCA_AS_OF=YYYY-MM-DD`。

## 环境要求

| 组件 | 要求 | 用途 |
| --- | --- | --- |
| Python | 3.10+ | 后端、数据导入及维护 |
| PostgreSQL | 14+，包含 `pg_trgm` 扩展 | 区划存储与查询 |
| Node.js | 20.x ≥20.19.0、22.x ≥22.13.0，或 ≥24.0.0 | 前端开发与构建，以 `web/package.json` 为准 |
| npm | 与 Node.js 配套 | 使用 `web/package-lock.json` 安装依赖 |
| Bash、curl、rsync | 执行抓取和刷新管线时需要 | 数据维护 |

macOS / Linux 可执行完整流程。Windows 的后端安装与运行可使用 PowerShell；涉及 Bash 的抓取和刷新流程使用 WSL。

## 安装与配置

以下命令在项目根目录执行。

### 1. 获取源码并安装后端依赖

```bash
git clone git@github.com:woshixieming/administrative-regionalization-china.git
cd administrative-regionalization-china

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
cp config/backend.example.ini config/backend.ini
```

Windows PowerShell 创建虚拟环境使用 `py -3 -m venv .venv`，激活使用 `.\.venv\Scripts\Activate.ps1`，复制配置使用 `Copy-Item config/backend.example.ini config/backend.ini`。

### 2. 创建数据库

以 PostgreSQL 管理员身份连接后执行：

```sql
CREATE ROLE divisions_owner LOGIN;
\password divisions_owner
CREATE DATABASE china_divisions OWNER divisions_owner;
```

`\password` 用于交互设置密码。导入账号需要在目标数据库中创建表和安装 `pg_trgm` 扩展的权限。

### 3. 填写参数配置

编辑 `config/backend.ini`，首次导入时填写导入账号：

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
user = divisions_owner
password =
sslmode = prefer
connect_timeout = 5
statement_timeout_ms = 30000
pool_min = 1
pool_max = 8
```

- `password` 填实际数据库密码；真实配置已被 Git 忽略，不应提交。
- `static_dir` 相对项目根目录；`statement_timeout_ms` 单位为毫秒，`connect_timeout` 单位为秒。
- `host`、`port` 分别配置后端监听地址与端口；数据库连接参数位于 `[postgres]`。
- 可以通过 `--config /绝对路径/config.ini` 指定配置。`backend.manage` 的 `--config` 放在子命令之前，例如 `python -m backend.manage --config /绝对路径/import.ini import-sql`。
- 后端运行可改用只读账号 `divisions_app`，授权脚本为 [database/permissions.sql](database/permissions.sql)。导入账号的配置单独保存，账号创建与切换步骤见 [权限配置](docs/postgresql.md#3-创建数据库与导入)。

### 4. 准备并导入数据

**克隆源码不会获得全量区划数据。** `database/data.sql`、原始数据和生成分片均不纳入 Git，需要单独传入，或从已有数据生成。

有完整 SQL 文件时，将其放入 `database/data.sql`，执行：

```bash
python -m backend.manage import-sql
```

也可以指定其他 SQL 文件位置：

```bash
python -m backend.manage import-sql --file /绝对路径/data.sql
```

SQL 包含建表、索引、全国区划及版本报告。Python 导入命令直接读取配置和 SQL，不需要 psql 客户端；若使用 psql，可执行 `psql -X -v ON_ERROR_STOP=1 -d china_divisions -f database/data.sql`，连接参数按实际账号设置。SQL 使用 `COPY FROM STDIN`，导入工具须支持相应数据段。

如果已有完整的 `web/public/data/`，可以先生成 SQL 再导入：

```bash
python -m backend.manage export-sql
python -m backend.manage import-sql
```

如果持有 `data/raw/` 和 `data/cache/`，可按下方“数据更新”中的 `refresh --skip-fetch` 流程重建并导入。

### 5. 构建并启动

保持 Python 虚拟环境已激活：

```bash
cd web
npm ci
npm run build
cd ..
python -m backend.server
```

访问 [http://127.0.0.1:3100](http://127.0.0.1:3100)。Python 后端同时提供前端构建产物和 API；网站运行需要连接已导入数据的 PostgreSQL。前端构建本身不要求连接数据库。

健康检查：

```bash
curl http://127.0.0.1:3100/api/health
```

响应包含 `status: "ok"` 和 `datasetVersion`，表示数据库连接与当前版本入口可用。

## 前端开发

先构建一次前端并保持 Python 后端运行，再打开另一个终端：

```bash
cd web
npm run dev
```

开发页面为 [http://127.0.0.1:3101](http://127.0.0.1:3101)，`/api/` 默认代理到 `http://127.0.0.1:3100`。
后端端口变化时，可在启动开发服务器时指定 `DIVISIONS_API_ORIGIN`：

```bash
DIVISIONS_API_ORIGIN=http://127.0.0.1:3200 npm run dev
```

## 数据更新

使用具有写入权限的导入账号配置，从项目根目录执行：

```bash
# 抓取四级数据，合并村级基线，校验并导入数据库
python -m backend.manage --config /绝对路径/import.ini refresh

# 不重新抓取，使用已有原始数据和村级基线重建后导入
python -m backend.manage --config /绝对路径/import.ini refresh --skip-fetch
```

更新流程为：接口探测与抓取 → 基线校验 → 数据合并 → 数量与父级关系校验 → SQL 生成 → PostgreSQL 事务导入 → 切换当前版本。

- 文件构建结果用于数据库导入，后端运行时从 PostgreSQL 取数。
- 事务提交前检查记录数量与父级外键；相同版本可重复导入，相同版本号但内容校验和不同会拒绝覆盖。
- 数据库导入失败时，服务继续使用原数据库版本；文件加工结果可能已经更新，可修复问题后重新生成并导入 SQL。
- 数据更新无需重启后端或重新构建页面；修改前端代码后需要重新执行 `npm run build`。
- 数据库保留已导入的历史版本，已打开页面继续使用原版本。数据库历史版本和文件快照分别管理。
- 页面状态面板显示导入时附带的公开任务状态，不实时展示尚未导入的抓取进度；私有维护日志位于 `data/logs/`。

如需定期更新，通过操作系统调度器运行 `backend.manage refresh`，指定项目工作目录、虚拟环境中的 Python 和导入账号配置文件。

### 人工修订

支持既有村级记录改名、同省调整所属乡级，保持村级总量不变。修订必须包含公开依据、理由和生效日期，并经过审核。以下参数需替换为已核实的信息：

```bash
python scripts/patches.py create --code 110101001001 --name '核实后的村级名称' \
  --source 'https://example.com/public-notice' --reason '经公开材料核实更名' \
  --effective-date YYYY-MM-DD

# 创建命令输出修订 ID；调整所属乡级时可使用 --street-code
python scripts/patches.py review '替换为修订ID' --decision approved --reason '核对公开依据通过'

# 审核完成后重建并发布数据库版本
python -m backend.manage --config /绝对路径/import.ini refresh --skip-fetch
```

只有审核通过且到达生效日期的修订才会应用，目标乡级必须已存在。原始冻结 CSV 不被修改；公开台账与导出保留修订依据，修订记录位于 `data/patches/`，应纳入版本管理。拒绝审核使用 `--decision rejected`，已审核记录的纠错通过新增修订完成。

## 数据库与 API

所有业务表位于 `divisions` schema：

| 表 | 内容 |
| --- | --- |
| `datasets` | 不可变数据版本、校验和及来源元数据 |
| `regions` | 五级区划、父级、祖先路径、历史标记和原始字段 |
| `documents` | 版本清单、变化报告、公开修订台账与任务状态 |
| `current_dataset` | 当前使用版本的指针 |

区划主键为 `(version, level, code)`，父级外键包含版本和层级，兼容跨层级同码容器；名称搜索、代码前缀、父子查询和祖先路径分别建立索引。

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 检查数据库与当前版本 |
| `GET /api/data/meta.json` | 当前版本元数据 |
| `GET /api/data/v/{version}/provinces.json` | 指定版本省列表 |
| `GET /api/data/v/{version}/l2/{provinceCode}.json` | 地级列表，省直辖容器由元数据补入 |
| `GET /api/data/v/{version}/l3/{cityCode}.json` | 县级列表 |
| `GET /api/data/v/{version}/l4/{前四位代码}.json` | 按县码分组的乡级列表 |
| `GET /api/data/v/{version}/l5/{前四位代码}.json` | 按乡码分组的村级列表 |
| `GET /api/search?version=...&q=东城&level=0&history=1` | 搜索，支持 `province` 省份筛选 |
| `GET /api/export?version=...&level=3&code=110101000000&history=1` | 所选区域及下级、生成完整路径所需的祖先记录 |
| `GET /api/status` | 当前导入版本附带的公开状态 |
| `GET /api/data/v/{version}/changes/summary.json` | 版本变化摘要 |

接口响应均来自数据库；路径中的 `.json` 是响应格式约定。错误以非 200 状态码和 `error` 字段返回。API 仅提供只读查询，数据写入通过受控的导入和修订流程完成。

## 目录结构

```text
backend/                   Python 后端、配置读取、数据库查询和导入工具
config/backend.example.ini 参数配置示例
config/backend.ini         本机真实配置，不纳入 Git
database/schema.sql        表结构与索引
database/permissions.sql   运行账号的只读授权
database/data.sql          全量导入 SQL，不纳入 Git
docs/postgresql.md         安装、权限和迁移的详细说明
web/app/                   Next.js 页面、样式和站点图标
web/components/            浏览、搜索、导出和版本面板
web/lib/                   前端 API 访问、路径和导出逻辑
web/out/                   前端构建产物
web/public/data/           数据加工结果与 SQL 生成源，不作为运行时数据源
scripts/                   抓取、基线恢复、数据构建、校验及修订工具
data/raw/                  原始四级数据及抓取报告
data/cache/                冻结村级基线
data/patches/              人工修订和审核记录
data/staging/              数据构建暂存目录
data/snapshots/            文件数据快照
data/logs/                 维护日志
tests/                     前端逻辑、维护流程和 PostgreSQL 测试
```

## 部署与迁移

部署时由服务管理器托管 `python -m backend.server --config /绝对路径/backend.ini`，将工作目录设为项目根目录，并使用虚拟环境中的 Python。对外访问可由 Nginx 等反向代理将页面和 `/api/` 请求统一转发到后端，并在入口配置 HTTPS。

迁移到其他电脑或服务器需要：

1. 项目源码和 `web/package-lock.json`。
2. 全量 `database/data.sql`，或 PostgreSQL 数据库备份。
3. 按目标环境填写的连接配置。
4. 如需继续抓取和重建，另带 `data/raw/`、`data/cache/` 及 `data/patches/`（存在时）。

运行网站不需要迁移依赖目录、开发缓存、文件快照或抓取日志。按“安装与配置”完成依赖安装、数据导入和前端构建后启动后端即可；已有数据库可直接配置连接。

## 验证

```bash
# Python 离线回归；未指定专用数据库时，跳过 PostgreSQL 集成测试
python -B -m unittest discover -s tests -v

# 前端 lint、类型检查、逻辑测试和生产构建
(cd web && npm run check)
```

PostgreSQL 集成测试需显式指定已导入数据的专用测试库配置：

```bash
DIVISIONS_TEST_CONFIG=/绝对路径/test.ini python -B -m unittest discover -s tests -p test_backend.py -v
```

测试库名必须以 `_test` 结尾；集成测试会重复导入并验证冲突回滚，只能指向隔离测试库。
项目的 [GitHub Actions](.github/workflows/check.yml) 在 push / pull request 时执行维护脚本、Python 离线回归和前端检查，数据库集成验证按上述方式单独运行。
