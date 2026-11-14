# 闸门契约与运行配置

GateSampler 使用两个 YAML 文件：**运行配置**（批次清单与输出位置）和**闸门契约**（字段 schema、抽样、断言、决策）。两者都只使用本地文件。

## 1. 运行配置

```yaml
version: "1.0"                         # 配置格式版本，当前仅 "1.0"
contract: ../fixtures/contracts/orders_contract.yaml
strict: false                          # 可选；加载警告升级为错误
contract_overrides:                   # 可选；深合并到契约，用于环境差异
  sampling:
    seed: 777
output:
  dir: out                             # 报告目录，相对当前工作目录
  ledger: out/gate_sampler.sqlite3     # 可选；默认 <dir>/gate_sampler.sqlite3
batches:
  - batch_id: batch_a                  # 小写 snake_case，3–64 位
    source: ../fixtures/batches/batch_a.csv
    format: csv                        # 可选；csv / jsonl，缺省按扩展名推断
    expected_rows: 24                  # 可选；来源清单声明行数
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `version` | 是 | 配置版本；不同版本不得混用 |
| `contract` | 是 | 契约 YAML 相对路径 |
| `strict` | 否 | 布尔；把加载警告升级为硬错误 |
| `contract_overrides` | 否 | 映射，递归覆盖契约字段 |
| `output.dir` | 否 | 默认 `out` |
| `output.ledger` | 否 | 默认 `<out>/gate_sampler.sqlite3` |
| `batches[].batch_id` | 是 | 同一配置内唯一 |
| `batches[].source` | 是 | CSV / JSONL 相对路径 |
| `batches[].format` | 否 | 显式 `csv` 或 `jsonl` |
| `batches[].expected_rows` | 否 | 供 `count` 断言使用 |

路径解析：`contract`、批次 `source`、断言 `baseline` 相对各自 YAML 文件所在目录；`output` 相对启动命令的工作目录。所有文本为 UTF-8。CSV 也接受 UTF-8 BOM。

## 2. 契约完整样例

```yaml
contract_version: "1.0"
gate_id: orders_gate
description: "订单批次入库前闸门"

schema:
  allow_extra_columns: false
  null_tokens: ["", "NULL", "null", "NA", "N/A", "None"]
  columns:
    order_id: { type: str, required: true }
    customer_id: { type: str, required: true }
    region: { type: str, required: true }
    amount: { type: float, required: true }
    status: { type: str, required: true }
    created_at: { type: date, required: true }

sampling:
  method: stratified
  seed: 20240117
  size: 14
  strata_field: region

assertions:
  - { id: row_count_match, type: count, tolerance: 0, severity: fail }
  - { id: order_id_unique, type: unique, columns: [order_id], full_scan: true, severity: fail }
  - { id: status_enum, type: domain, column: status, allowed: [paid, pending, refunded, cancelled] }
  - { id: amount_bounds, type: range, column: amount, min: 0, max: 5000 }
  - { id: region_null_rate, type: nullrate, column: region, max_rate: 0.10 }
  - { id: customer_duplicate_rate, type: duplicate, columns: [customer_id], max_rate: 0.15, severity: warn }
  - id: region_drift
    type: drift
    column: region
    baseline: ../batches/baseline/batch_base.csv
    threshold: 0.12
    metric: max_share_delta
    severity: warn

decision:
  block_on_fail: true
  review_on_warn_count: 1
  review_on_skip_count: null
```

## 3. `schema`

- `columns`：字段名到 `{type, required}` 的映射；也接受 `order_id: str` 简写。
- `type`：`int`、`float`、`bool`、`str`、`date`。`date` 接受 ISO `YYYY-MM-DD`，输出为规范化 ISO 字符串。
- `required`：仅用于结构声明与报告；空值率应交由 `nullrate` 断言判定。
- `allow_extra_columns`：`false` 时多余列产生 `GS-LOAD-102` 警告；`true` 时保留但不参与类型转换。
- `null_tokens`：字符串匹配后统一转换为 `None`；空字符串是默认空值标记。
- 缺少声明列会产生 `GS-LOAD-101`；`strict: true` 时加载直接失败。
- 列类型转换失败会产生 `GS-LOAD-103` 警告，并保留原始值；范围断言可进一步将其计为值域违规。

## 4. `sampling`

| 字段 | 说明 |
| --- | --- |
| `method` | `systematic` / `stratified` / `reservoir` |
| `seed` | 必填非负 64 位整数；禁止隐式随机 |
| `size` | 固定样本量，正整数；与 `fraction` 二选一 |
| `fraction` | `0 < fraction <= 1`，样本量向上取整；与 `size` 二选一 |
| `strata_field` | 仅 `stratified` 必填，且须在 schema 中声明 |

三层行为：

- `systematic`：按 `n / k` 等距取值，起点由 seed 随机化。
- `stratified`：按字段值分层；样本量按层规模分配并保证每层至少 1 条；目标样本量小于非空层数时报 `GS-SMP-001`；每层使用 `sha256(seed:stratum)` 派生种子做系统抽样。
- `reservoir`：单遍 Algorithm R；结果按原始行索引排序以保持输出稳定。

样本结果记录 `method`、`seed`、请求量/实际量、行索引、样本记录号、层分配明细和 `sample_fingerprint`。同一输入、seed、方法、参数必须逐字节一致。

## 5. 断言

所有断言公共字段：`id`（唯一 snake_case）、`type`、`severity`（`fail` 默认，或 `warn`）。断言按声明顺序执行。每个结果包含：

```json
{
  "assertion_id": "status_enum",
  "type": "domain",
  "status": "fail",
  "code": "GS-GATE-103",
  "metric": 2,
  "threshold": {"allowed": ["paid"], "invalid_count": 0},
  "evidence": {"scope": "sample", "rows": [4, 9], "invalid_values": ["void"]},
  "message": "…"
}
```

`status` 取值：`pass`、`fail`、`warn`、`skip`。`severity` 决定违规时输出 `fail` 还是 `warn`；`skip` 表示缺少字段、空样本、基线缺失等无法计算的情况，原因写入 `evidence`。

| 类型 | 必要参数 | 语义与范围 |
| --- | --- | --- |
| `count` | 可选 `expected`、`tolerance`（默认 0） | 实际批次行数对比 `expected`，未给则用批次的 `expected_rows`；只读批次元数据 |
| `unique` | `columns` 非空列表；`full_scan` 布尔 | 主键组合唯一；先查样本。`full_scan: true` 时显式退回全量，这是唯一允许全表扫描的断言；不开启时证据明确说明样本不能证明整批唯一 |
| `domain` | `column`、`allowed` 非空标量列表 | 样本中非空值必须属于集合；空值由 nullrate 负责 |
| `range` | `column`，以及 `min`/`max`/`min_length`/`max_length` 至少一项 | 数值边界包含端点；长度按 `str(value)` 计算；非数值计入违规 |
| `nullrate` | `column`、`max_rate`（0–1） | 样本空值数/样本量；恰好等于阈值视为通过 |
| `duplicate` | `columns`、`max_rate`（0–1） | 样本内重复率 = (总行数−不同组合数)/总行数；`None` 视为一个取值 |
| `drift` | `column`、`baseline`、`threshold`；可选 `bins`、`metric`（默认 `max_share_delta`） | 样本与基线的一阶分布差异；分类字段含 `<null>`，数值字段按基线范围固定分箱频次。`max_share_delta`/`total_variation` 的 `threshold` 取 0–1；`chi_square` 的 `threshold` 为卡方统计量，仅要求非负 |

`drift` 支持三种 `metric`：

- `max_share_delta`（默认）：各类/箱占比绝对差的最大值，阈值是占比差，量纲 0–1。
- `total_variation`：总变差距离，即各类/箱占比绝对差之和除以 2，复用同一组 `top_deltas`；阈值量纲同为 0–1，语义为整列分布层面的总偏移而非单类偏移。
- `chi_square`：Pearson 卡方统计量，按基线期望频次做低频箱合并——期望频次低于 5 的箱按自然顺序并入相邻箱（数值列按箱下标、分类列按标签顺序，最末欠频箱回并到前一箱）；样本总量与基线总量不同时，期望频次按样本总数缩放。数值列基线只有单一取值（`high == low`）的退化分箱会被确定性地折叠为单个 `degenerate_bin`，自由度记为 0。合并后的箱集合（`bins`）、`degrees_of_freedom`、各箱 `expected_frequency` 与 `chi_square_contribution` 均写入 `evidence` 供复核；未知 `metric` 以 `GS-CFG-030` 拒绝。

`drift` 基线是本地 CSV/JSONL，使用同一 schema 加载；基线缺失时断言在独立调用中返回 `skip`，完整运行时由运行器报告 `GS-LOAD-001`，避免静默忽略审计依赖。

断言错误码固定为 `GS-GATE-101` 至 `GS-GATE-107`，分别对应上表顺序；`skip` 也保留对应类型码，`evidence` 中写明原因。

## 6. `decision`

```yaml
decision:
  block_on_fail: true          # 任一 fail → block；false 时 fail → review
  review_on_warn_count: 1      # warn 数 ≥ 该值 → review，正整数
  review_on_skip_count: null   # 可选；skip 数 ≥ 该值 → review
```

规则优先级：拦截 → fail 转人工 → warn 转人工 → skip 转人工 → 放行。详细语义与理由链见 [DECISIONS.md](DECISIONS.md)。

## 7. 版本与校验

- 配置 `version` 和契约 `contract_version` 目前都必须为 `"1.0"`；否则 `GS-CFG-002`。
- 未知契约版本不会与台账中的旧版本混跑；重放会重新校验快照中的契约版本。
- 所有配置失败都带稳定码和字段/数组位置，例如 `GS-CFG-031 [contract.assertions[2].type]: 未知断言类型: mystery`。
- 未声明的断言字段、决策字段、schema 列参数会被拒绝，避免拼写错误静默生效。
- `contract_overrides` 走深合并；列表（例如 `assertions`）整体替换，不按索引合并。
