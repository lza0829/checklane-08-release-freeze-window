# 04-checklane（检道）

Checklane — 数据质检通道：入检批次装载 → 规则命中与严重度裁决 → 隔离/放行签发 → 签发清单与源批对账。

**不是** Feed 多源归并（Feedstitch），**不是** 作业依赖编排（Batchgate），**不是** CDC 变更流按序落库（Changeflow）。
**也不是** 报表看板 / CRUD 后台 / 电商订单 / OA 工单。

## 启动

`ash
docker compose up --build
`

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5400 |
| API | http://localhost:10400 |

本地开发（可选）：

`ash
# backend
cd backend && pip install -r requirements.txt
set DATA_DIR=..\\data
uvicorn app.main:app --reload --port 10400

# frontend
cd frontend && npm install && npm run dev
`

## 主链

登记数据集与规则 → 装载夹具批次入检 → 按规则裁决放行/隔离 → 签发到精选区 → 批次报告与各列表数字同源

## 种子说明

- **Orders Snapshot**：JSONL 商品快照，含必填缺失与类型错误行，用于演示入检与隔离。
- **Inventory Snapshot**：库存快照，独立数据集，用于第二路规则演示。

打开「入检台」选数据集跑一次，再看隔离区 / 精选区 / 批次报告计数是否与本次报告一致。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 放行冻结窗（release_freeze_window）

切换窗口期间，即使规则全部通过也不能写入精选区：

- 冻结窗可配置起止时间，边界口径为 `start_at <= now < end_at`（**含起点，不含终点**），配置落本机 SQLite（`settings` 表），刷新/重启仍在。
- 窗内入检**照常完整跑规则与隔离**，但通过行不写精选区，批次进入 `frozen_held`（检过未签发 / 冻结挂起）态，`released_count = 0`。
- 窗内任何补签/二次签发入口（`POST /api/batches/{id}/sign`）一律 **409 拒绝**；窗结束（或解除窗口）后才能对挂起批次补签。
- 补签**不重跑规则、不改动隔离结果**，只把入检时已判定 `pass` 的行写入精选区，并在报告中追加 `post_freeze_sign` 事件。
- 报告区分三类计数：已检通过 `passed`、因冻结未签发 `held_by_freeze`、最终签发 `released`，并以 `curated_batch_matches` / `freeze_zero_release` 校验与精选区变化互证。
- 页面「放行冻结窗」可配置窗口、查看挂起批次并在解冻后补签；「批次」页展示挂起态与未签发数。

演示路径：配置覆盖当前时刻的窗口 → 入检可见零签发、批次挂起 → 窗内点补签被挡（409）→ 解除/等待窗口结束 → 补签成功，精选区出现本批通过行。

## 0-1 切口（未实现，见题单）

当前仓库已实现「单批入检 + 基础规则 + 放行签发」主链与「放行冻结窗」。严重度整批阻断、规则包版本、抽样入检、跨字段规则、批次重开、金标对照、批内重复键、规则临时豁免、跨数据集参照检查等均在 ackend/app/modules/* 留空包，按题单逐题换主链路实现。
