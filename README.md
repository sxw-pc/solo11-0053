# 闸门抽样台（GateSampler）

**一句话定位：** 在本地批次数据写入目标库之前，按声明式闸门契约做可复现抽样、执行七类闸门断言，并把整批判定为 `pass / block / review` 的离线准入引擎与审计台账。

它不是爬虫、调度器或 RPA；不做字段脱敏；不做指标口径校准；不做湖仓快照/时间旅行；不做表关联发现。它只回答一个问题：**这批数据该放行、拦截，还是转人工？为什么？**

## 快速上手

环境：Python 3.11+，本地安装 `PyYAML`、`click`、`Flask`、`pytest`（可执行一次 `python -m pip install -e .`）。运行期不访问网络。

```bash
python -m gatesampler run --config configs/demo.yaml
python -m gatesampler serve
python -m pytest -q
```

第一条命令生成 `out/reports/*.json`、`out/reports/*.md` 与 `out/gate_sampler.sqlite3`；第二条在 `http://127.0.0.1:5000/` 提供只读台账；第三条运行离线测试。另可用 `out-strict/` 查看严格加载与蓄水池抽样演示：

```bash
python -m gatesampler run --config configs/strict.yaml
```

## 演示输出

```text
[batch_a] rows=24 cols=6 sha256=981ca3fffc3de365… sample=stratified/14 seed=20240117 decision=pass
[batch_b] rows=20 cols=6 sha256=336758344506c795… sample=stratified/14 seed=20240117 decision=review
[batch_bad] rows=24 cols=6 sha256=fe0a32c1805ff835… sample=stratified/14 seed=20240117 decision=block
ledger=out/gate_sampler.sqlite3
完成：3 个批次判定已落账。
```

`batch_a` 全部通过；`batch_b` 因样本分布相对基线漂移被标记 `warn` 并转人工；`batch_bad` 同时触发行数不符、主键重复、枚举非法、值域越界、空值率超限等 fail 而拦截。所有 fixture 均为合成占位数据。

## 目录说明

```text
configs/                 demo / strict 运行配置
fixtures/contracts/      闸门契约 YAML
fixtures/batches/        CSV、JSONL、基线批次（确定性合成）
gatesampler/config*.py   配置与契约校验、稳定错误码
gatesampler/loader.py    CSV/JSONL 加载、类型转换、结构警告
gatesampler/sampler.py   三种纯函数抽样器
gatesampler/assertions/  七类可插拔断言
gatesampler/decision.py  三态决策与理由链
gatesampler/report.py    字节稳定 JSON / Markdown 报告
gatesampler/ledger.py    SQLite 台账与查询
gatesampler/replay.py    基于台账快照、种子与配置的重放
gatesampler/runner.py    单批/多批执行编排
gatesampler/server/      本机只读 Flask 页面
scripts/make_fixtures.py 确定性 fixture 生成器
tests/                   配置、加载、抽样、断言、决策、台账、重放、CLI、服务
docs/                    CONTRACT.md / DECISIONS.md
```

## 配置关系

运行配置 `configs/demo.yaml` 引用一个契约文件并列出批次：

```yaml
version: "1.0"
contract: ../fixtures/contracts/orders_contract.yaml
strict: false
output: { dir: out, ledger: out/gate_sampler.sqlite3 }
batches:
  - { batch_id: batch_a, source: ../fixtures/batches/batch_a.csv, expected_rows: 24 }
```

契约声明字段类型、必填字段、抽样参数、断言阈值和决策规则；完整字段说明见 [docs/CONTRACT.md](docs/CONTRACT.md)。`contract`、`source`、`baseline` 相对各自 YAML 文件所在目录解析；`output` 相对当前工作目录。契约与配置均带版本号，当前仅接受 `"1.0"`，混用版本会直接报 `GS-CFG-002`。

## 抽样与断言

| 方法 | 说明 |
| --- | --- |
| `systematic` | 等距抽样，随机起点由显式 seed 派生 |
| `stratified` | 按 `strata_field` 分层，按比例分配并在每层内用派生种子抽样；目标样本量低于非空层数时报 `GS-SMP-001` |
| `reservoir` | 单遍 Algorithm R 蓄水池抽样 |

样本量用 `size`（固定条数）或 `fraction`（比例，向上取整）声明，两者只能出现一个。相同 `seed + 输入 + 方法 + 参数` 得到逐字节一致的抽样结果，并记录 `sample_fingerprint`。

七类断言：`count`（行数守恒）、`unique`（主键唯一，可显式 `full_scan: true` 全量校验）、`domain`（枚举合法）、`range`（数值/长度边界）、`nullrate`（字段空值率）、`duplicate`（字段重复率）、`drift`（相对基线的最大占比偏移）。每条断言输出 `status / metric / threshold / evidence / code / message`；样本内命中行以记录号定位。`unique` 若不做全量会在证据中明确写出“样本范围无法证明整批唯一”的说明。

## 决策、报告与台账

决策规则由契约声明：任一 `fail` 且 `block_on_fail: true` 时拦截；警告数量达到 `review_on_warn_count` 时转人工；否则放行。可选 `review_on_skip_count` 对跳过项设置人工阈值。理由链逐条指向贡献该决策的断言与规则码。

JSON 报告排序键固定、浮点统一舍入、无时间戳，因此同一输入与配置重复运行得到相同 SHA-256。Markdown 摘要包含批次头、抽样参数、断言表、决策理由、非通过断言证据和错误码统计。SQLite 台账保存批次元数据、seed、抽样参数、每个断言结果、决策、配置快照与报告哈希。

## 重放与审计

```bash
python -m gatesampler query --ledger out/gate_sampler.sqlite3 --decision review
python -m gatesampler query --ledger out/gate_sampler.sqlite3 --batch-id batch_bad
python -m gatesampler replay --ledger out/gate_sampler.sqlite3 --run-id <run-id>
python -m gatesampler replay --ledger out/gate_sampler.sqlite3 --batch-id batch_bad
```

重放使用台账中的配置快照、显式 seed 和原始来源路径重新加载、抽样、断言与决策，并核对来源 SHA-256、样本指纹、决策、断言细节及报告 SHA-256；不一致时命令退出码为 `1`。判定 ID 由批次、来源哈希与配置指纹内容寻址生成，相同输入重复运行会幂等更新同一判定，不依赖隐式随机状态或当前时间。

## 错误码

配置与契约：

| 码段 | 含义 |
| --- | --- |
| `GS-CFG-001` | YAML 不可读或解析失败 |
| `GS-CFG-002` | 配置/契约版本不受支持 |
| `GS-CFG-003` | 必需字段缺失或字符串非法 |
| `GS-CFG-004` | 字段类型/范围非法 |
| `GS-CFG-010` / `011` | schema 列定义非法 / 列类型不支持 |
| `GS-CFG-020` / `021` | 抽样参数非法 / seed 缺失或越界 |
| `GS-CFG-030`..`032` | 断言参数非法 / 类型未知 / id 重复 |
| `GS-CFG-040` | 决策规则非法 |
| `GS-CFG-050` / `051` | 批次定义非法 / batch_id 重复 |
| `GS-CFG-060` | contract_overrides 非法 |

加载与断言：

| 码段 | 含义 |
| --- | --- |
| `GS-LOAD-001`..`005` | 文件不可读、格式/编码、CSV、JSONL、行对象错误 |
| `GS-LOAD-101`..`105` | 缺列、多列、类型转换、空批次、重复表头（加载警告，strict 升级为错误） |
| `GS-SMP-001` | 抽样参数在运行期不可满足 |
| `GS-GATE-101`..`107` | 对应七类断言；`status` 表示通过、失败、警告或跳过 |

决策、台账与服务：

| 码段 | 含义 |
| --- | --- |
| `GS-DEC-001`..`006` | 拦截、warn 转人工、放行、fail 转人工、skip 转人工、fail 数量达阈值拦截 |
| `GS-LGR-001`..`004` | 重放参数缺失、记录不存在、快照缺失、台账查询失败 |
| `GS-SRV-001` / `002` | 非回环监听地址拒绝 / 服务启动失败 |

## 离线与合规

- 运行期零网络请求、零 DNS、零遥测；页面无 CDN、无外部字体、无脚本、无上传与写操作。
- `serve` 只允许绑定 `127.0.0.1`、`localhost` 或 `::1`，并校验 `Host` 头；非本机 Host 返回 `403` 与明确提示。
- 不连接外部数据库、对象存储或消息队列，不写回目标库，不提供登录或权限体系。
- 仓库无真实密钥、令牌、公司名、域名；fixture 由 `scripts/make_fixtures.py` 用固定种子生成，可重复再生。

## 测试

```bash
python -m pytest -q
```

覆盖契约错误码、CSV/JSONL 加载与异常路径、三种抽样确定性、七类断言各分支、三态决策与理由链、SQLite 查询、fixture 重放一致性、报告字节稳定性、CLI 端到端以及本机只读服务/非本机 Host 拒绝。测试不依赖网络，通常在数秒内完成。
