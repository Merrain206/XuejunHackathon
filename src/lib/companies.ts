export type CompanyMetric = {
  label: string;
  value: string;
  note: string;
};

export type Company = {
  id: string;
  name: string;
  fullName: string;
  ticker: string;
  exchange: string;
  exchangeCode: "SSE" | "SZSE";
  industry: string;
  monogram: string;
  listedAt?: string;
  metrics: CompanyMetric[];
  suggestedQuestions: string[];
};

export const demoSuggestedQuestions = [
  "你的收入结构发生了什么变化？",
  "你最近真的赚钱吗？",
  "目前最值得关注的风险是什么？",
  "你的员工喜欢吃水果吗？",
];

export const generalSuggestedQuestions = [
  "最近营业收入和归母净利润表现如何？",
  "最近一期总资产、总负债、净资产和资产负债率是多少？",
  "董事会构成和中小股东制衡机制如何？",
  "审计意见、审计机构和关键审计事项是什么？",
  "历史分红、股份回购和未来分红政策如何？",
  "员工持股和股权激励情况如何？",
  "目前有哪些可观察的风险信号？",
  "你的员工喜欢吃水果吗？",
];

export const companies: Company[] = [
  {
    id: "688583",
    name: "思看科技",
    fullName: "思看科技（杭州）股份有限公司",
    ticker: "688583",
    exchange: "上交所科创板",
    exchangeCode: "SSE",
    industry: "工业级 3D 视觉数字化",
    monogram: "S",
    listedAt: "2025-01-15",
    metrics: [
      { label: "2023 主营业务收入", value: "2.72 亿", note: "招股书口径" },
      { label: "便携式扫描仪占比", value: "57.87%", note: "2023" },
      { label: "跟踪式产品占比", value: "26.58%", note: "2023" },
    ],
    suggestedQuestions: demoSuggestedQuestions,
  },
  {
    id: "600570",
    name: "恒生电子",
    fullName: "恒生电子股份有限公司",
    ticker: "600570",
    exchange: "上交所主板",
    exchangeCode: "SSE",
    industry: "金融科技软件",
    monogram: "H",
    metrics: [
      { label: "公告状态", value: "已收录", note: "数据库" },
      { label: "Evidence", value: "可用", note: "可追溯" },
      { label: "问答方式", value: "动态提问", note: "公告问答" },
    ],
    suggestedQuestions: generalSuggestedQuestions,
  },
  {
    id: "000066",
    name: "中国长城",
    fullName: "中国长城科技集团股份有限公司",
    ticker: "000066",
    exchange: "深交所主板",
    exchangeCode: "SZSE",
    industry: "计算产业与信息安全",
    monogram: "C",
    metrics: [
      { label: "公告状态", value: "已收录", note: "数据库" },
      { label: "Evidence", value: "可用", note: "可追溯" },
      { label: "问答方式", value: "动态提问", note: "公告问答" },
    ],
    suggestedQuestions: generalSuggestedQuestions,
  },
  {
    id: "300558",
    name: "贝达药业",
    fullName: "贝达药业股份有限公司",
    ticker: "300558",
    exchange: "深交所创业板",
    exchangeCode: "SZSE",
    industry: "创新药研发与商业化",
    monogram: "B",
    metrics: [
      { label: "公告状态", value: "已收录", note: "数据库" },
      { label: "Evidence", value: "可用", note: "可追溯" },
      { label: "问答方式", value: "动态提问", note: "公告问答" },
    ],
    suggestedQuestions: generalSuggestedQuestions,
  },
];

export function getCompany(companyId: string) {
  return companies.find((item) => item.id === companyId);
}
