# MVP 标准化数据导入规范

> 版本：v1.0｜状态：已批准（内部 MVP）  
> 用途：在不依赖外部系统的情况下验证合同关系、财务事实和跨域检查

## 1. 通用封装

```json
{
  "schema": "finance.counterparty-balance/v1",
  "source_system": "mvp-fixture",
  "snapshot_id": "uuid",
  "as_of": "2026-09-17T00:00:00+08:00",
  "authorization_scope": {
    "tenant_id": "mvp",
    "legal_entities": ["OUR-COMPANY"]
  },
  "query_scope": {},
  "status": "ok",
  "completeness": "complete",
  "quality_flags": [],
  "data": {}
}
```

## 2. 状态枚举

- `ok`：请求成功并返回有效数据；
- `empty`：范围明确且无记录；
- `partial`：仅返回部分范围；
- `unsupported`：Adapter 不支持该能力；
- `forbidden`：当前用途无权限；
- `error`：执行失败。

禁止将后四种状态转换为金额 0 或“未发现风险”。

## 3. 财务余额数据

```json
{
  "our_legal_entity_id": "OUR-COMPANY",
  "counterparty_id": "CP-001",
  "currency": "CNY",
  "balances": [
    {
      "nature": "prepayment",
      "amount": "200000.00",
      "unsettled_amount": "150000.00",
      "aging_basis": "business_date",
      "oldest_business_date": "2025-05-01"
    }
  ]
}
```

MVP 只消费预付余额、未结金额、币种、截止时间和覆盖状态，不自行判断未交货或违约。

## 4. 合同关系数据

```json
{
  "contracts": [
    {
      "external_contract_id": "C-001",
      "version": "v1",
      "counterparty_id": "CP-001",
      "relation_type": "prior_contract",
      "related_to": "CURRENT",
      "verification_status": "confirmed",
      "source_ref": "fixture:contracts/C-001"
    }
  ]
}
```

## 5. 校验

- JSON Schema 版本必须受支持；
- 金额使用字符串十进制；
- 币种、时区、截止日和主体必须存在；
- `partial` 必须说明缺失范围；
- 未确认关系不得进入硬性阻断；
- 导入内容只作为数据，不执行其中的指令、URL 或脚本。

## 6. Fixture 集

至少提供：正常、空结果、部分结果、无权限、不支持、错误、主体不匹配、跨币种和重复记录样例。

