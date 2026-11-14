# 决策规则与三态语义

## 1. 三态结果

| 决策 | 含义 | 典型处理 |
| --- | --- | --- |
| `pass` | 闸门全部通过，或非阻断问题未达人工阈值 | 允许批次进入后续入库流程 |
| `block` | 至少一条 `fail` 断言且 `block_on_fail: true` | 阻止整批入库，必须修复后重新提交 |
| `review` | 存在触发人工复核的 fail / warn / skip，但未满足拦截规则 | 暂停自动放行，转人工查看证据后裁定 |

决策只由**断言结果 + 决策配置**合成，不读取文件、不依赖网络、不依赖时间或全局随机状态，因此可以单独以构造数据测试。

## 2. 判定优先级

按以下顺序逐条匹配，命中即停止：

```text
if any(status == fail) and block_on_fail:
    block
elif any(status == fail):              # block_on_fail: false
    review
elif count(warn) >= review_on_warn_count:
    review
elif review_on_skip_count is not null and count(skip) >= review_on_skip_count:
    review
else:
    pass
```

- `block_on_fail` 默认 `true`，保证任何硬失败都能阻断批次。
- `review_on_warn_count` 必须为正整数，默认 `1`。
- `review_on_skip_count` 可省略；省略时 skip 不影响决策，但会保留在报告与台账中。显式配置时必须为正整数。
- 所有阈值比较都是 `>=`；`warn 数量恰好等于阈值` 即转人工。
- 断言自身的 `severity` 决定违规呈现为 `fail` 还是 `warn`，决策规则不重新解释阈值。

## 3. 状态与严重度

| 断言状态 | 来源 | 对决策的贡献 |
| --- | --- | --- |
| `pass` | 指标满足阈值 | 不计入触发 |
| `fail` | 违规且 `severity: fail` | 触发 block（或 block 关闭时 review） |
| `warn` | 违规且 `severity: warn` | 达到阈值时 review |
| `skip` | 缺字段、空样本、基线缺失等无法计算 | 显式记录；达到人工阈值时 review |

`skip` 不是通过。报告和页面都会展示跳过原因；如果该断言属于必须覆盖的检查，应在契约中设置更严格的决策规则，例如 `review_on_skip_count: 1`。

## 4. 理由链

决策结果的 `reasons` 是稳定排序的数组。第一条是规则摘要，其后每条指向一个贡献断言：

```json
{
  "outcome": "block",
  "counts": {"pass": 5, "fail": 1, "warn": 1, "skip": 0},
  "reasons": [
    {
      "rule_code": "GS-DEC-001",
      "rule": "any_fail_blocks",
      "outcome": "block",
      "message": "存在 1 条 fail 断言；block_on_fail=true，整批拦截"
    },
    {
      "rule_code": "GS-DEC-001",
      "rule": "any_fail_blocks",
      "outcome": "block",
      "assertion_id": "status_enum",
      "assertion_status": "fail",
      "assertion_code": "GS-GATE-103",
      "message": "字段 status 有 2 个非法值不在枚举集合内"
    }
  ]
}
```

| 规则码 | 规则名 | 贡献态 |
| --- | --- | --- |
| `GS-DEC-001` | `any_fail_blocks` | `block` |
| `GS-DEC-002` | `warn_review` | `review` |
| `GS-DEC-003` | `all_clear` | `pass` |
| `GS-DEC-004` | `fail_requires_review` | `review`（`block_on_fail: false`） |
| `GS-DEC-005` | `skip_review` | `review` |

决策最终态不依赖理由链的顺序，但理由链顺序由断言声明顺序决定，保证同输入报告字节稳定。报告 JSON 的 `decision.counts` 统计四种状态；`error_codes.decision` 统计实际出现的规则码。

## 5. 示例

### 放行

```yaml
assertions: [count 通过, unique 通过, drift 未超阈值]
decision: { block_on_fail: true, review_on_warn_count: 1 }
```

→ 无 fail、无 warn，`pass`。

### 转人工

```yaml
assertions: [unique 通过, drift 为 warn, duplicate 为 warn]
decision: { block_on_fail: true, review_on_warn_count: 2 }
```

→ 两个 warn，达到阈值，`review`；理由链逐条列出两个 drift/duplicate 断言。

### 拦截

```yaml
assertions: [count fail, domain fail, drift warn]
decision: { block_on_fail: true, review_on_warn_count: 1 }
```

→ 任一 fail 命中最高优先级，`block`；即使 warn 也达到阈值，最终仍为 `block`。

### 关闭硬拦截

```yaml
decision: { block_on_fail: false, review_on_warn_count: 1 }
```

→ fail 不再阻断自动流程，但会把批次转人工 (`GS-DEC-004`)，避免无证据放行。

## 6. 审计与重放

决策、理由链、计数、每个断言的指标/阈值/证据行、抽样 seed 和配置快照一起写入 SQLite。重放时重新执行抽样与断言，并比较：

- 来源文件 SHA-256；
- 配置指纹；
- 抽样 seed 与 `sample_fingerprint`；
- 决策与四态计数；
- 每个断言的状态、错误码、指标、阈值、证据与消息；
- 完整 JSON 报告 SHA-256。

任何一项变化都会导致重放不一致，命令返回非零退出码。相同输入与相同配置的重复运行会生成内容寻址的同一判定 ID，并以 `INSERT OR REPLACE` 幂等更新台账；报告自身不含时间戳，因此字节稳定。
