"""普通投资者问题的 Evidence 主题目录。

这里只放确定性的主题、问法和公告检索锚点；不包含模型逻辑。数据库扩展脚本与
动态问答共用这份目录，避免“库里抽了但后端不认识”或反过来。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InvestorTopic:
    metric: str
    label: str
    category: str
    question_keywords: tuple[str, ...]
    evidence_keywords: tuple[str, ...]
    document_types: tuple[str, ...] = ()
    max_per_company: int = 8


INVESTOR_TOPICS: tuple[InvestorTopic, ...] = (
    # 公司治理与股东权利
    InvestorTopic("board_composition", "董事会构成", "company", ("董事会几席", "董事会一共几席", "董事会有几席", "董事会多少席", "董事会构成", "董事人数", "几名董事"), ("董事会由", "董事会成员", "名董事组成", "位董事组成"), ("articles_of_association", "annual_report", "board")),
    InvestorTopic("board_nomination", "董事提名与席位", "company", ("提名几席", "提名董事", "董事提名", "实控人几席", "控股股东几席"), ("提名董事", "董事候选人", "提名委员会", "有权提名"), ("articles_of_association", "shareholders_meeting", "board")),
    InvestorTopic("actual_controller", "实际控制人与控制关系", "company", ("实控人", "实际控制人", "谁控制", "控制权", "控股股东是谁"), ("实际控制人", "控股股东", "控制关系"), ("annual_report", "prospectus", "other")),
    InvestorTopic("minority_protection", "中小股东制衡机制", "company", ("小股东", "中小股东", "制衡机制", "累积投票", "网络投票", "单独计票"), ("中小股东", "累积投票", "网络投票", "单独计票"), ("articles_of_association", "shareholders_meeting", "legal_opinion")),
    InvestorTopic("independent_director", "独立董事机制", "company", ("独立董事", "独董", "独立性"), ("独立董事", "独立性"), ("independent_director", "annual_report", "articles_of_association")),
    InvestorTopic("shareholder_structure", "股东与股权结构", "company", ("股东结构", "前十大股东", "前十名股东", "持股比例", "第一大股东"), ("前十名股东", "前十大股东", "股东名称", "持股比例"), ("annual_report", "q1_report", "semiannual_report")),
    InvestorTopic("management_ownership", "董监高持股", "company", ("高管持股", "高层持股", "管理层持股", "董监高持股", "董事持股"), ("董事、监事和高级管理人员持股", "董监高持股", "持股变动"), ("annual_report", "semiannual_report", "other"), 10),
    InvestorTopic("share_pledge", "股东股份质押", "company", ("股权质押", "股份质押", "质押比例", "有没有质押"), ("股份质押", "质押股份", "累计质押"), ("annual_report", "other")),
    InvestorTopic("share_capital", "总股本与股份变动", "financial", ("总股本", "有多少股", "股本变化", "股份总数", "摊薄"), ("总股本", "股本", "股份总数", "股份变动"), ("annual_report", "q1_report", "semiannual_report", "other"), 10),

    # 审计、内控与合规
    InvestorTopic("audit_opinion", "审计意见", "company", ("审计意见", "审计结论", "非标意见", "无保留意见"), ("标准无保留意见", "无保留意见", "非标准审计意见", "保留意见"), ("audit_report", "annual_report")),
    InvestorTopic("audit_firm", "审计机构", "company", ("审计机构", "审计事务所", "会计师事务所", "更换审计", "更换会计师"), ("会计师事务所", "审计机构", "续聘", "变更会计师事务所"), ("audit_report", "annual_report", "shareholders_meeting"), 12),
    InvestorTopic("key_audit_matter", "关键审计事项", "company", ("关键审计事项", "审计重点", "审计关注"), ("关键审计事项",), ("audit_report", "annual_report"), 12),
    InvestorTopic("internal_control_opinion", "内部控制审计意见", "company", ("内控意见", "内部控制意见", "内控审计", "内部控制有效"), ("内部控制审计意见", "内部控制是有效的", "内部控制缺陷"), ("internal_control", "audit_report", "annual_report")),
    InvestorTopic("regulatory_penalty", "监管处罚与整改", "company", ("监管处罚", "行政处罚", "监管措施", "被处罚", "整改情况"), ("行政处罚", "监管措施", "纪律处分", "整改"), ("annual_report", "other")),
    InvestorTopic("litigation", "诉讼与仲裁", "company", ("诉讼", "仲裁", "官司", "法律纠纷"), ("重大诉讼", "重大仲裁", "诉讼事项", "仲裁事项"), ("annual_report", "other")),
    InvestorTopic("guarantee", "对外担保", "financial", ("对外担保", "担保余额", "给谁担保", "违规担保"), ("对外担保", "担保余额", "违规担保"), ("annual_report", "other")),

    # 股东回报、资本动作与员工利益
    InvestorTopic("dividend_history", "历史分红", "financial", ("历史分红", "历年分红", "过去分红", "过去三年分红", "分红多少", "分到多少", "持有", "派息多少", "普通股东能分多少", "每股分红", "每10股"), ("现金分红", "每10股派", "权益分派", "利润分配方案"), ("profit_distribution", "annual_report"), 12),
    InvestorTopic("dividend_per_share", "每股分红方案", "financial", ("分到多少", "持有", "每股分红", "每10股", "派息多少"), ("每10股派", "每10 股派", "每 10 股派", "每 10股派", "每股派"), ("profit_distribution", "annual_report", "semiannual_report", "other"), 12),
    InvestorTopic("dividend_policy", "分红政策", "company", ("分红政策", "未来分红", "利润分配政策", "分红条件"), ("利润分配政策", "现金分红条件", "未来三年股东回报", "分红政策"), ("articles_of_association", "annual_report", "other"), 10),
    InvestorTopic("share_repurchase", "股份回购", "financial", ("回购", "股份回购", "回购多少", "回购进展"), ("回购股份", "股份回购", "回购金额", "累计回购"), ("other", "board", "annual_report"), 12),
    InvestorTopic("employee_holding_plan", "员工持股计划", "company", ("员工持股", "持股计划", "覆盖多少员工", "员工持股情况"), ("员工持股计划", "持股计划份额"), ("other", "board", "shareholders_meeting"), 12),
    InvestorTopic("employee_holding_platform", "员工持股平台", "company", ("持股平台", "员工平台", "几个持股平台", "平台名称", "每人出资", "持股份额"), ("员工持股平台", "持股平台"), ("prospectus", "annual_report", "other"), 12),
    InvestorTopic("equity_incentive", "股权激励", "company", ("股权激励", "限制性股票", "股票期权", "激励对象"), ("股权激励", "限制性股票", "股票期权", "激励对象"), ("other", "board", "annual_report"), 12),
    InvestorTopic("employee_count", "员工人数与结构", "company", ("员工人数", "有多少员工", "人员结构", "员工结构", "研发人员占比"), ("员工数量", "员工人数", "在职员工", "员工专业构成", "研发人员"), ("annual_report", "prospectus"), 10),
    InvestorTopic("executive_compensation", "董监高薪酬", "company", ("高管薪酬", "董事薪酬", "管理层薪酬", "董监高薪酬"), ("董事、监事和高级管理人员", "从公司获得的税前报酬", "薪酬"), ("annual_report",)),

    # 经营质量、供应链和关联关系
    InvestorTopic("labor_outsourcing", "劳务外包", "business", ("劳务外包", "外包供应商", "外包合同", "假外包", "真派遣"), ("劳务外包", "外包供应商", "外包合同", "外包费用"), ("prospectus", "annual_report", "other"), 12),
    InvestorTopic("labor_dispatch", "劳务派遣", "business", ("劳务派遣", "派遣员工", "派遣比例", "派遣用工", "真派遣", "假外包", "外包还是派遣"), ("劳务派遣", "派遣员工", "派遣用工"), ("prospectus", "annual_report", "other"), 10),
    InvestorTopic("major_customer", "主要客户集中度", "business", ("主要客户", "前五大客户", "客户集中度", "第一大客户"), ("前五名客户", "前五大客户", "主要客户", "客户集中度"), ("annual_report", "prospectus"), 10),
    InvestorTopic("major_supplier", "主要供应商集中度", "business", ("主要供应商", "前五大供应商", "供应商集中度", "第一大供应商"), ("前五名供应商", "前五大供应商", "主要供应商", "供应商集中度"), ("annual_report", "prospectus"), 10),
    InvestorTopic("related_party", "关联方关系", "company", ("关联方", "是不是关联方", "关联关系", "关联企业"), ("关联方", "关联关系", "关联企业"), ("related_transaction", "annual_report", "prospectus"), 10),
    InvestorTopic("related_transaction", "关联交易", "financial", ("关联交易", "关联采购", "关联销售", "关联交易金额"), ("关联交易", "关联采购", "关联销售"), ("related_transaction", "annual_report"), 12),
    InvestorTopic("business_products", "主营业务与产品", "business", ("主要产品", "主营业务", "靠什么赚钱", "业务构成", "产品线"), ("主要产品", "主营业务", "业务构成", "产品和服务"), ("annual_report", "prospectus"), 10),
    InvestorTopic("competition", "行业竞争与市场地位", "business", ("竞争格局", "竞争对手", "市场地位", "市场占有率", "行业排名"), ("竞争格局", "主要竞争对手", "市场占有率", "市场地位"), ("annual_report", "prospectus"), 10),
    InvestorTopic("patents", "专利与知识产权", "business", ("专利", "知识产权", "发明专利", "软件著作权"), ("发明专利", "专利", "软件著作权", "知识产权"), ("annual_report", "prospectus"), 10),
    InvestorTopic("rd_personnel", "研发人员", "company", ("研发人员", "研发团队", "研发员工", "技术人员占比"), ("研发人员", "研发团队", "技术人员"), ("annual_report", "prospectus"), 10),
    InvestorTopic("major_contract", "重大合同与订单", "business", ("重大合同", "大额合同", "在手订单", "订单情况", "重大合同金额"), ("重大合同", "在手订单", "合同金额", "重大销售合同"), ("annual_report", "other"), 10),
    InvestorTopic("subsidiaries", "子公司与投资版图", "company", ("子公司", "控股公司", "参股公司", "投资版图"), ("主要子公司", "控股子公司", "参股公司"), ("annual_report", "prospectus", "other"), 10),
    InvestorTopic("acquisition", "并购与资产交易", "business", ("并购", "收购", "资产重组", "买了什么公司"), ("收购", "重大资产重组", "购买资产", "股权转让"), ("annual_report", "other", "board"), 10),
    InvestorTopic("product_pipeline", "产品与研发管线", "business", ("研发管线", "产品管线", "临床进展", "新产品进展", "在研项目"), ("研发管线", "产品管线", "临床试验", "在研项目", "新产品"), ("annual_report", "prospectus", "other"), 12),
    InvestorTopic("segment_revenue", "分业务收入结构", "financial", ("收入结构", "分产品收入", "分业务收入", "主营构成"), ("分行业", "分产品", "主营业务分", "营业收入构成"), ("annual_report", "semiannual_report"), 12),
    InvestorTopic("overseas_revenue", "境外收入", "financial", ("海外收入", "境外收入", "国外收入", "海外业务"), ("境外", "海外收入", "国外"), ("annual_report", "semiannual_report", "prospectus")),

    # 资金、债务和项目
    InvestorTopic("total_liabilities", "负债合计", "financial", ("总负债", "负债合计", "一共有多少负债"), ("负债合计", "总负债"), ("annual_report", "semiannual_report", "q1_report", "q3_report"), 12),
    InvestorTopic("total_equity", "所有者权益合计", "financial", ("净资产", "所有者权益", "股东权益合计"), ("所有者权益合计", "股东权益合计"), ("annual_report", "semiannual_report", "q1_report", "q3_report"), 12),
    InvestorTopic("cash_balance", "货币资金", "financial", ("现金有多少", "货币资金", "账上现金", "现金储备"), ("货币资金",), ("annual_report", "semiannual_report", "q1_report", "q3_report"), 12),
    InvestorTopic("goodwill", "商誉", "financial", ("商誉", "商誉减值", "并购商誉"), ("商誉", "商誉减值"), ("annual_report", "semiannual_report", "audit_report"), 12),
    InvestorTopic("asset_impairment", "资产减值", "financial", ("资产减值", "减值损失", "减值风险"), ("资产减值损失", "信用减值损失", "减值准备"), ("annual_report", "semiannual_report", "audit_report"), 12),
    InvestorTopic("debt_structure", "有息负债与债务结构", "financial", ("有息负债", "债务结构", "短期借款", "长期借款", "偿债压力"), ("有息负债", "短期借款", "长期借款", "一年内到期"), ("annual_report", "semiannual_report"), 12),
    InvestorTopic("fund_raising", "募集资金", "financial", ("募集资金", "募资多少", "资金用途"), ("募集资金", "募集资金总额", "募集资金净额"), ("fund_raising", "annual_report", "prospectus"), 12),
    InvestorTopic("project_progress", "募投项目进展", "business", ("募投项目", "项目进展", "投资项目", "项目延期"), ("募投项目", "募集资金投资项目", "项目进度", "延期"), ("fund_raising", "annual_report"), 12),
    InvestorTopic("government_grant", "政府补助", "financial", ("政府补助", "政府补贴", "补助多少"), ("政府补助",), ("annual_report", "semiannual_report", "other")),
    InvestorTopic("tax_incentive", "税收优惠", "financial", ("税收优惠", "所得税率", "高新技术企业优惠", "税务风险"), ("税收优惠", "高新技术企业", "企业所得税税率"), ("annual_report", "prospectus"), 10),
    InvestorTopic("environment", "环保与排放", "business", ("环保", "污染", "碳排放", "环境处罚"), ("环境保护", "污染物", "碳排放", "环保处罚"), ("annual_report", "other"), 10),
    InvestorTopic("safety", "安全生产", "business", ("安全生产", "安全事故", "生产事故"), ("安全生产", "安全事故", "生产事故"), ("annual_report", "other"), 10),
    InvestorTopic("data_security", "数据安全与网络安全", "business", ("数据安全", "网络安全", "信息安全", "隐私保护"), ("数据安全", "网络安全", "信息安全", "个人信息保护"), ("annual_report", "other"), 10),
)


TOPIC_BY_METRIC = {topic.metric: topic for topic in INVESTOR_TOPICS}
QUESTION_RULES = tuple((topic.question_keywords, (topic.metric,)) for topic in INVESTOR_TOPICS)
METRIC_LABELS = {topic.metric: topic.label for topic in INVESTOR_TOPICS}


__all__ = ["INVESTOR_TOPICS", "METRIC_LABELS", "QUESTION_RULES", "TOPIC_BY_METRIC", "InvestorTopic"]
