# 劳动人事规则目录

## 1. 关系与主体

### `employment.relationship.classification`
核对合同名称与实际管理、工作内容、报酬、持续性、工作时间地点、工具和组织从属性；疑似错分时保留全部事实并转人工。

### `employment.employer.identity`
检查用人单位、用工主体、发薪/社保主体、关联公司、派遣或外包主体是否一致。

### `employment.worker.eligibility`
检查身份、年龄、许可、兼职/利益冲突和岗位准入材料；敏感信息遵循最小必要。

## 2. 订立与入职

### `employment.offer.conditions`
检查录用条件、背景核验、入职时间、岗位、薪酬及与劳动合同冲突。

### `employment.written_contract`
检查书面合同签订、期限、双方、签署和文本交付事实；缺失时创建补材料/法律复核任务。

### `employment.term_probation`
检查固定/无固定期限、试用期、试用工资、录用条件和试用考核证据是否相互匹配。

### `employment.job_location`
检查岗位、职责、工作地点、汇报关系和合理变更边界，避免无限单方调整。

### `employment.compensation`
拆解基本工资、奖金、提成、津贴、补贴、股权和支付时间；区分固定报酬与附条件激励。

### `employment.working_time`
检查工时制度、排班、休息、加班审批与实际记录；制度名称不证明已获得必要审批或实际合规。

### `employment.benefits_social_insurance`
检查社保、公积金、假期、福利和补充保险约定；合同放弃不能自动覆盖强制义务。

## 3. 履行与制度

### `employment.handbook.validity`
检查制度制定、协商/程序、公布、版本、生效、签收和适用主体，避免把制度内容与制定程序混同。

### `employment.performance_discipline`
检查绩效目标、考核周期、证据、改进计划、奖惩和申辩程序；主观评价不能替代事实。

### `employment.leave_absence`
检查法定与企业假期、请假程序、医疗期、旷工认定和证明材料；地方口径需版本化依据。

### `employment.change`
检查调岗、调薪、地点、工时和主体变化的依据、协商、通知和实际执行。

### `employment.personal_information`
检查招聘、在职、监控、考勤、健康和离职数据的目的、范围、权限、保存和共享。

### `employment.health_safety`
检查职业健康、安全培训、特殊岗位、事故和报告义务；依赖实际岗位和行业事实。

## 4. 保密、成果和限制

### `employment.confidentiality`
检查保密信息、例外、使用、披露、返还销毁、期限和商业秘密保护措施。

### `employment.ip.invention`
区分职务成果、非职务成果、既有成果、发明人/作者身份、报酬奖励和进一步协助。

### `employment.noncompete`
检查适用人员、业务范围、地域、期限、补偿、启动/放弃、违约责任和实际执行；地方司法口径需核验。

### `employment.nonsolicit_conflict`
检查利益冲突、兼职、客户/员工招揽和在职限制，避免超出合理业务保护目的。

### `employment.training.service_period`
检查专项培训事实、费用证据、服务期、违约金计算、已履行期间和与竞业限制的区分。

## 5. 解除、终止与离职

### `employment.termination.grounds`
固定拟采用的解除/终止理由，并核对事实、合同、制度和规范依据；不得事后替换理由。

### `employment.termination.evidence`
检查绩效、违纪、医疗、客观情况、协商、送达和其他证据的完整性与矛盾。

### `employment.termination.procedure`
检查通知、申辩、工会或其他程序、审批、期限和送达；具体适用依赖场景和地区。

### `employment.termination.payment`
计算工资、奖金、未休假、补偿/赔偿、竞业补偿和其他应付款，保留期间、基数、公式和已付款。

### `employment.exit.handover`
检查工作、资产、账号、数据、文件和客户交接；交接义务不得无依据冻结法定或无争议款项。

### `employment.certificate.records`
检查离职证明、档案社保转移、权限关闭和记录保留，区分内部记录与对外表述。

## 6. 特殊关系

### `employment.dispatch_outsourcing`
区分劳务派遣、业务外包和直接用工，检查管理边界、资质、人员指挥、费用和责任。

### `employment.consultant_intern`
检查顾问、兼职、退休返聘和实习的真实关系、报酬、保险、成果和安全责任。

### `employment.special_protection`
识别可能涉及特殊保护的人员或期间，只输出需核验事项，不凭敏感特征自行下结论。

## 7. 联动

- 录用条件—试用期—考核—试用解除；
- 岗位—工时—考勤—加班—薪酬；
- 制度版本—公示签收—违纪事实—解除程序；
- 培训费用—服务期—已履行期间—违约金；
- 保密—职务成果—竞业限制—离职交接；
- 解除理由—证据—程序—付款—送达。
