# 数据分析实验与数据应用服务平台

面向指标口径管理、分析实验设计、OLAP查询、数据看板构建与数据API发布的数据应用平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_def` | 指标 | 指标编号、指标名称、计算公式 |
| 维度管理 | `dimension` | 分析维度 | 维度编号、维度名称、维度类型 |
| 维度字典交换 | `dimension_exchange` | 交换包 | 版本、来源、生效期、层级代码、父级代码、别名 |
| 数据集管理 | `dataset` | 分析数据集 | 数据集编号、数据集名称、来源类型 |
| 分析实验 | `experiment` | 实验方案 | 方案编号、实验名称、分析数据集 |
| OLAP查询 | `query_builder` | 查询模板 | 模板编号、模板名称、查询语句 |
| 数据看板 | `dashboard` | 看板配置 | 看板编号、看板名称、包含图表 |
| 图表配置 | `chart` | 图表 | 图表编号、图表名称、图表类型 |
| 数据API发布 | `data_service` | 数据服务 | 服务编号、服务名称、输出数据集 |
| 智能告警 | `alert` | 告警规则 | 规则编号、告警名称、监控指标 |
| 分析用户组 | `user_group` | 用户组 | 组编号、组名称、组描述 |
| 数据快照 | `snapshot` | 快照记录 | 快照编号、数据集名称、快照时间 |
| 任务调度 | `scheduler` | 调度任务 | 任务编号、任务名称、调度类型 |
| 缓存管理 | `cache` | 缓存配置 | 缓存编号、缓存目标、缓存策略 |
| 分析管道 | `pipeline` | 分析节点 | 节点编号、输入数据集、分析算子 |
| 分析模型 | `model` | 模型配置 | 模型编号、模型名称、模型类型 |
| 数据集市 | `data_mart` | 集市表 | 表编号、表名称、主题域 |
| 地区归并 | `region_reconcile` | 归并批次 | 订单号、地区代码、归并结果、待映射原因 |
| 数据导出 | `export` | 导出任务 | 任务编号、导出对象、导出格式 |
| 数据审计 | `audit` | 审计记录 | 记录编号、操作类型、操作人 |

## 维度字典交换与地区归并

维度管理页与数据集市页下方各有一块交换区，也直接暴露为接口：

| 接口 | 说明 |
| --- | --- |
| `GET /api/dimension/exchange/dictionary/export?format=csv` | 导出本地当前字典，可直接回传导入 |
| `GET /api/dimension/exchange/packages/{id}/export?format=csv` | 按交换包导出分卷 |
| `POST /api/dimension/exchange/packages` | 导入分卷包（`volumes[].content` 为 CSV 或 `volumes[].rows`） |
| `GET /api/dimension/exchange/comparison`、`/comparison.csv` | 版本对照：上级、别名、生效期、源文件行号 |
| `GET /api/dimension/exchange/alias-conflicts`、`.csv` | 冲突别名对照 |
| `POST /api/dimension/exchange/packages/{id}/aliases` | 为地区代码追加别名 |
| `POST /api/data_mart/region/reconcile` | 按 `by_source` / `hold_overlap` 重算归并 |
| `GET /api/data_mart/region/conflicts`、`.csv` | 冲突别名、生效期冲突与受影响订单对照 |

导入分卷至少需要 `层级代码、父级代码、生效日期、别名` 四列，可选 `维度字段、名称、失效日期`。
包级字段：`version`、`source`、`field`（默认地区）、`effective_from`、`effective_to`。

规则要点：

- 同一交换包按内容指纹去重，重复上传不新增成员、不重复登记成员变更。
- 每个成员记录源文件与物理行号；跨分卷重复代码只保留首次出现的一行并给出诊断。
- 父级未收录或层级成环的边不计入有效层级，这些成员归并时按待映射处理。
- 两版生效期重叠即视为版本冲突，并列出同一代码在两版里的上级/名称差异。
- 归并口径：`by_source` 按订单来源选覆盖业务日的版本；`hold_overlap` 对重叠期订单暂缓归并。
  两种口径每笔订单都只产出一条结果，`mapped_count + pending_count == order_total`。

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
