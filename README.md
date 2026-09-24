# 投苗数量与重量口径修复

这是水产养殖管理系统，后端维护塘口、养殖批次、投苗、投喂、水质、用药、成本和出塘销售资料，前端提供日常录入与周期分析页面。数据默认保存在 SQLite 文件中。

## 测试命令

在仓库根目录执行：

```bash
python3 -m unittest discover -s tests -v
```

## 编译与构建命令

先安装前端依赖，再检查后端并构建前端：

```bash
python3 -m compileall -q backend/app
npm --prefix frontend install --legacy-peer-deps
npm --prefix frontend run build
```

本地启动可使用 `docker compose up --build`。开发环境不得提交真实账号、连接凭据或生产数据。

## 投苗计量口径（v1：stocking-measure-v1）

投苗列表、批次追溯、成活率分析、前端表单共用同一口径实现，禁止各处自行换算：

- 后端单一事实源：`backend/app/stocking_policy.py`（`GET /api/stocking-records/policy/` 返回同版本说明）
- 前端单一事实源：`frontend/src/utils/stockingPolicy.ts`，版本常量与后端一致

规则：

- 尾数：正整数（1 ~ 10 亿尾），禁止零、负数、小数、NaN/Infinity。
- 每尾克重：单位克/尾，范围 `0.01 ~ 10000` 克，按 **0.01 克、ROUND_HALF_UP（四舍五入）** 归整。
- 总重量（公斤）只能由明细派生，**不可手工录入不一致的值**：

  ```
  total_weight_kg = quantity(尾) × weight_per_unit(克/尾) ÷ 1000
  ```

  按 **0.001 公斤（1 克）、ROUND_HALF_UP** 归整；提交值与派生值相差超过 1 克容差直接 422。
- 成活率不再写死“0.5 公斤/尾”，而是用投苗记录的**按尾数加权平均单重**反推：

  ```
  survival_qty = floor(harvest_weight_kg × 1000 ÷ avg_weight_per_unit_g)
  survival_rate = survival_qty ÷ 投苗总尾数
  ```

  投苗明细缺单重时返回 `survival_estimable=false` 及提示，不输出误导性成活率。

## 投苗事实的更正、撤销与防重复

- 新增支持 `client_token` 幂等令牌：重复提交/并发双发只产生一条增量（唯一索引 + 冲突回查）。
- **更正**：`POST /api/stocking-records/{id}/correct/`，必须填原因；旧版本标记 `superseded` 并保留原值，
  生成递增新版本（`revision`、`supersedes_id`、`root_id`），支持 `expected_revision` 乐观锁；
  并发更正由“同一 root 仅一个 active 版本”的部分唯一索引保证只有一方成功（409 表示版本已变化）。
- **撤销**：`POST /api/stocking-records/{id}/void/`，必须填原因，软删（`status=void`）且原值留痕。
- 旧的 `PUT`/`DELETE` 已停用（返回 410），不得直接改写或物理删除。
- 每次创建/更正/撤销/修复都向 `stocking_record_revisions` 追加一条**只增不改**的台账，
  可用 `GET /api/stocking-records/{id}/revisions/` 查看。
- 已参与周期分析的批次（登记实际收获日期、状态为 harvested/closed、或已有出塘销售记录）
  视为事实锁定：新增/更正/撤销投苗一律 409。
- 分批补苗用 `record_type=supplement` 与首次投苗区分；列表、追溯、分析默认只统计 `active` 版本。

## 历史数据诊断与修复（可重复执行）

```bash
# 只读诊断，输出 JSON 报告（负数/非有限数/缺单重/总重不符/超范围）
DATABASE_URL="sqlite:///./aquaculture.db" python -m app.stocking_diagnostics          # 在 backend/ 目录

# 按明细重算总重量（只改派生字段，必要时归整单重精度），原值与原因写入修复台账
DATABASE_URL="sqlite:///./aquaculture.db" python -m app.stocking_diagnostics --fix
```

二次执行 `--fix` 不会再次改动或重复写台账；负数、非有限数、超范围等无法安全自动修复的问题
列入 `needs_manual`（退出码 2），需人工核实后通过“更正”处理。前端“投苗记录”页也提供
“诊断 / 按明细修复总重量”入口（`GET /api/stocking-records/diagnostics/`、
`POST /api/stocking-records/diagnostics/repair/`）。

应用启动时会自动执行幂等迁移（`backend/app/migrations_stocking.py`）：为旧库补列、
回填状态/口径版本、建立唯一索引，并为历史记录补录创建台账。
