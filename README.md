# 投苗数量与重量口径修复

这是水产养殖管理系统，后端维护塘口、养殖批次、投苗、投喂、水质、用药、成本和出塘销售资料，前端提供日常录入与周期分析页面。数据默认保存在 SQLite 文件中。

## 本次修复的核心口径

采购、生产、分析三处共用 **同一份计量事实源**
（`backend/app/services/metrics.py`，前端镜像 `frontend/src/utils/metrics.ts`），
版本号通过 `GET /api/metrics/stocking` 发布，前端启动时与后端核对：

| 量 | 单位 | 精度 | 规则 |
| --- | --- | --- | --- |
| 尾数 `quantity` | 尾 | 正整数 | 1 ~ 100,000,000，禁止负数、零、小数、非有限数 |
| 每尾克重 `weight_per_unit` | 克/尾 | 0.01 克（四舍五入 ROUND_HALF_UP） | 0.01 ~ 5000 克，必须为有限正数 |
| 总重量 `total_weight` | 公斤（千克） | 0.001 公斤（克级） | **只能由明细派生，API 不接受写入** |

派生公式（唯一合法来源）：

```
total_weight_kg = round( quantity * round(weight_per_unit_g, 2) / 1000 , 3)
```

成活率不再固定按 0.5 公斤/尾反推，而是使用出塘记录登记的 **出塘均重（克/尾）**：

```
survival_count  = round( harvest_weight_kg * 1000 / harvest_weight_per_unit_g )
survival_rate(%) = round( survival_count / 有效投苗尾数 * 100 , 2)
```

未登记出塘均重时成活率返回 `null` 并附 `survival_rate_note`，绝不臆造。
多次出塘按重量加权得到综合出塘均重；反推尾数超过投苗尾数时夹断。

## 更正、撤销与防重复

- **新增/分批补苗**：`POST /api/stocking-records/`，建议带 `Idempotency-Key`
  请求头；同键重复提交或并发重试只产生一次增量（回放首次结果）。
- **总重量**：请求体不接受 `total_weight`，一律服务端按公式派生并打口径版本标记。
- **更正**：`PUT /api/stocking-records/{id}/` 必须携带 `expected_version`
  （乐观锁）与 `reason`；服务端条件更新（CAS），版本不符返回 `409`，
  原值写入 `stocking_record_events` 与 `corrected_from`，`version` 递增。
- **撤销**：`POST /api/stocking-records/{id}/void/`（软撤销，必须填原因），
  撤销记录保留原值、不再计入任何汇总；`DELETE` 物理删除已禁用（405）。
- **事实锁定**：批次已出塘/已关闭或已登记出塘销售后，记录参与了周期分析，
  禁止就地更正/撤销，只能新增补苗记录冲销，接口返回 `409`。
- 变更历史：`GET /api/stocking-records/{id}/events/`
  （created / corrected / voided / repaired，含原值、新值、原因、版本、口径版本）。
- 统一汇总：`GET /api/stocking-records/totals/{batch_id}/`，
  投苗列表、批次追溯、周期分析都只统计 `status=active` 的记录并引用同一口径版本。
- 出塘记录新增可选（前端必填）`weight_per_unit`（出塘均重，克/尾），
  范围 0.01 ~ 20000 克，同样禁止负数与非有限数。

## 历史数据诊断与修复（可重复执行）

```bash
# 只读诊断（不改库）
python -m backend.app.scripts.repair_stocking
# 应用修复（幂等，可反复执行；第二次零变更）
python -m backend.app.scripts.repair_stocking --apply
```

也提供 HTTP 入口（生产可加权限控制）：

```
GET  /api/diagnostics/stocking           # 诊断报告
POST /api/diagnostics/stocking/repair    # 默认 dry_run；body {"apply": true} 落库
```

修复只做**确定性**动作：按有效明细重算总重量、规范整数尾数、回填
`status/version/metrics_version`，并为每次实际修复写 `repaired` 审计事件。
缺少每尾克重、负数、非有限数等无法安全派生的记录**只报告、不臆造**，
需人工补录或冲销。启动时 `backend/app/migrations.py` 会在旧 SQLite 上
幂等 `ALTER TABLE` 加列/建索引并回填默认值，可重复执行。

## 测试命令

在仓库根目录执行：

```bash
python3 -m unittest discover -s tests -v
```

覆盖：克/公斤/尾精度与 ROUND_HALF_UP 舍入、非法值拒绝、总重量派生、
幂等去重、并发更正 409、更正/撤销审计留痕、事实锁定、按真实出塘均重
计算成活率、列表/追溯/分析三处口径一致、旧库迁移与诊断修复幂等。

## 编译与构建命令

先安装前端依赖，再检查后端并构建前端：

```bash
python3 -m compileall -q backend/app
npm --prefix frontend install --legacy-peer-deps
npm --prefix frontend run build
```

本地启动可使用 `docker compose up --build`。开发环境不得提交真实账号、连接凭据或生产数据。
