# 融资担保规则目录

## 1. 主体、角色与授权

### `finance.parties.roles`
识别借款人、贷款人、担保人、抵押/出质人、债务人、账户行、代理行和受益主体。

### `finance.capacity.authorization`
检查章程、董事会/股东会、法定代表人、关联担保、回避和授权文件；效力问题转专业复核。

### `finance.group.relationship`
核对集团、关联、控制和受益关系，识别交叉担保、资金用途和利益冲突。

### `finance.document_system`
建立授信、借款、提款、担保、登记、账户、费用、豁免和修订的文件关系与优先级。

## 2. 主债权与资金

### `finance.facility.amount`
区分承诺额度、可用额度、已提款、未偿本金、循环/非循环、分币种和最高余额。

### `finance.purpose.use`
检查资金用途、禁止用途、支付路径、受托支付和用途变更。

### `finance.drawdown.conditions`
检查提款条件、申请、证明、重复陈述、先决条件和贷款人裁量。

### `finance.term.maturity`
检查起息、到期、宽限、展期、提前还款和循环期限。

### `finance.repayment.waterfall`
检查分期、现金清扫、扣款账户、偿还顺序、部分还款和币种兑换。

## 3. 利息、费用和税费

### `finance.interest.rate`
检查固定/浮动利率、基准、加点、重定价、日计数、复利、罚息和替代基准。

### `finance.fees`
检查承诺、安排、管理、代理、顾问、提前还款和其他费用的触发与重复。

### `finance.default_interest`
检查逾期利息、罚息、复利、违约金和损害赔偿叠加及法律效力疑点。

### `finance.tax.grossup`
检查代扣税、税收补足、印花/登记、费用承担和税务变化。

### `finance.calculation.statement`
检查贷款人计算、账户证明、异议期限和明显错误纠正，保留独立复算权。

## 4. 陈述、承诺和财务约束

### `finance.representations`
检查主体、授权、财务、资产、诉讼、合规、税务、债务和担保陈述及重复频率。

### `finance.information_reporting`
检查报表、预算、证书、重大事项、诉讼、违约和审计报告的范围与期限。

### `finance.affirmative_covenants`
检查持续经营、许可、税费、保险、资产维护、合规和担保完善义务。

### `finance.negative_covenants`
检查新增债务、担保、资产处置、分红、投资、关联交易、合并和业务变化限制。

### `finance.financial_covenants`
检查指标定义、测试日、合并范围、会计准则、调整项、阈值、补救和权益注入。

### `finance.most_favored_crossdefault`
检查最惠贷款人、交叉违约/加速、门槛、争议债务和集团范围。

## 5. 违约与救济

### `finance.events_default`
检查不付款、违反承诺、虚假陈述、交叉违约、无力偿债、诉讼、执行、控制权和重大不利变化。

### `finance.material_adverse_change`
检查定义、判断主体、客观性、范围和重复适用，避免无法界定的绝对裁量。

### `finance.cure_periods`
检查付款/非付款违约补救期、通知、可补救性和重复违约。

### `finance.acceleration_cancellation`
检查取消额度、停止提款、宣布到期、自动加速、部分加速和通知。

### `finance.setoff_debit`
检查抵销、直接扣款、账户合并、币种兑换、通知和争议款。

### `finance.enforcement_costs`
检查律师费、处置费、评估、保全和实现债权费用的范围、合理性和证明。

## 6. 保证

### `finance.guarantee.type`
识别一般/连带、独立/从属、最高额、共同保证和保证方式；不明确时按现行规范核验。

### `finance.guarantee.scope`
检查主债权、利息、罚息、违约金、赔偿和实现费用，以及金额/币种上限。

### `finance.guarantee.period`
检查保证期间起算、到期、主债务变更、展期和债权主张证据。

### `finance.guarantee.amendment_consent`
检查主合同金额、期限、利率、债务人和债权转让变化对担保及同意要求的影响。

### `finance.coguarantee.recourse`
检查共同保证、份额、内部追偿、顺位和反担保。

## 7. 抵押、质押和其他担保

### `finance.collateral.ownership`
核对担保物所有权、共有、权利限制、瑕疵和第三方同意。

### `finance.collateral.description`
检查担保物识别、范围、未来财产、孳息、替代物和最高额关系。

### `finance.collateral.valuation_coverage`
检查估值日、方法、折扣、波动、补充担保和覆盖率，区分合同价值和可实现价值。

### `finance.perfection.registration`
检查登记、交付、通知、控制或其他设立/对抗要求及其完成证据。

### `finance.priority`
检查在先负担、顺位、共享担保、债权人协议和后续融资。

### `finance.collateral.maintenance`
检查保管、保险、处分、收益、检查和价值维护。

### `finance.collateral.enforcement`
检查触发、通知、处置方式、估值、价款分配、剩余返还和不足追偿。

### `finance.countersecurity`
检查反担保对象、范围、期限、登记、追偿和与主担保联动。

## 8. 特殊融资

### `finance.factoring`
检查基础应收、真实性、转让通知、追索/无追索、回购、稀释和债务人抗辩。

### `finance.finance_lease`
检查租赁物选择、所有权、交付验收、租金、维修保险、留购和提前终止。

### `finance.account_control`
检查监管账户、收入归集、提款条件、划扣和余额控制。

### `finance.debt_restructuring`
检查确认余额、展期、减免条件、新旧债务、担保存续、违约恢复和权利保留。

## 9. 变更、转让与结束

### `finance.assignment_participation`
检查债权转让、参与、债务转移、通知、受让人范围和数据披露。

### `finance.amendment_waiver`
检查修改、豁免、保留权利、一次性处理和担保人/其他债权人同意。

### `finance.prepayment_release`
检查自愿/强制提前还款、费用、担保释放条件、登记注销和文件返还。

### `finance.confidentiality_data`
检查财务信息、征信/信用数据、集团共享、潜在受让人和监管披露。

## 10. 联动

- 额度—提款—余额—利息费用—还款瀑布；
- 主债权—保证范围—保证期间—主合同变更；
- 担保物—权属—登记—顺位—估值—执行；
- 财务指标—测试日—违约—补救—加速；
- 债权转让—通知—担保随附—数据披露；
- 展期/豁免—担保人同意—登记更新—权利保留。
