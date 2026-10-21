# 开发态评测报告：legal.price.definition.dev@1.0.0

> 本报告只汇总已提供的结构化 observations 与开发预期的匹配情况；不生成观察值，不代表真实模型质量、正式金标或发布批准。

## 总览

| 指标 | 结果 |
|---|---:|
| 案例数 | 10 |
| 已提供 observations | 10 |
| 匹配 / 不匹配 | 10 / 0 |
| 描述性匹配率 | 100.0% |
| 评测门状态 | `blocked_no_approved_thresholds` |
| 未覆盖类别 | 无 |

## 逐例结果

| 案例 | 类别 | 状态 | 差异 |
|---|---|---|---|
| price-consistent-001 | positive | 已观察且匹配 | 无 |
| price-tax-conflict-001 | conflict | 已观察且匹配 | 无 |
| price-fixed-percentage-conflict-001 | conflict | 已观察且匹配 | 无 |
| price-missing-denominator-001 | missing_material | 已观察且匹配 | 无 |
| price-source-conflict-001 | conflict | 已观察且匹配 | 无 |
| price-multicurrency-001 | boundary | 已观察且匹配 | 无 |
| price-repeated-stage-001 | boundary | 已观察且匹配 | 无 |
| price-no-amount-001 | negative | 已观察且匹配 | 无 |
| price-injection-001 | security | 已观察且匹配 | 无 |
| price-evidence-missing-001 | security | 已观察且匹配 | 无 |

## 完整性指纹

- 规则引用：`legal.price.definition@1.0.0`；规则 SHA-256：`eec02a45364178721f2894eac1c7bcd1a1bcc1b451f3e94b866e8d65be8f3b93`
- 套件 SHA-256：`d3a0447267bb42ba8edbe45de74cf179fcf16790be1548bf8e05013f52ad4c0d`；Fixture manifest SHA-256：`18b2719aefcc80d98cf7ee63aa74760a3c2010f5d9fe471971a73469a59b253c`
- Observations SHA-256：`4a7fcf4ab51eef721c11afd7283615e94db7ae062388279c5cc5909a16afc329`
- 报告 SHA-256：`017edf398be3b31d2c813646ac6e65438166703a0779632fd674e1e2741b6aa2`

报告自哈希用于完整性核验，不是签名或审批证明。
