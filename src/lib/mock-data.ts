import type { AskResponse, Evidence } from "./types";

export const company = {
  id: "688583",
  name: "思看科技",
  fullName: "思看科技（杭州）股份有限公司",
  ticker: "688583",
  exchange: "上交所科创板",
  industry: "工业级 3D 视觉数字化",
  listedAt: "2025-01-15",
};

const prospectusUrl =
  "https://static.sse.com.cn/stock/disclosure/announcement/c/202408/001845_20240816_R2YE.pdf";
const halfYearReportUrl =
  "https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2025-08-28/688583_20250828_9F77.pdf";

export const demoSuggestedQuestions = [
  "你的收入结构发生了什么变化？",
  "你最近真的赚钱吗？",
  "目前最值得关注的风险是什么？",
  "你的员工喜欢吃水果吗？",
];

const structureEvidence: Evidence[] = [
  {
    id: "EV-STR-001",
    category: "business",
    period: "2021—2023",
    content: "便携式 3D 扫描仪收入持续增长，但在主营业务收入中的占比连续下降。",
    documentTitle: "首次公开发行股票并在科创板上市招股说明书（注册稿）",
    sourcePage: 323,
    sourceQuote:
      "便携式3D扫描仪销售收入由12,579.02万元增至15,722.33万元，占主营业务收入比例由78.19%降至57.87%。",
    sourceUrl: prospectusUrl,
  },
  {
    id: "EV-STR-002",
    category: "business",
    period: "2021—2023",
    content: "跟踪式 3D 视觉数字化产品收入和收入占比快速提升。",
    documentTitle: "首次公开发行股票并在科创板上市招股说明书（注册稿）",
    sourcePage: 323,
    sourceQuote:
      "跟踪式3D视觉数字化产品销售收入由1,893.69万元增至7,222.66万元，占比由11.77%升至26.58%。",
    sourceUrl: prospectusUrl,
  },
  {
    id: "EV-STR-003",
    category: "financial",
    period: "2021—2023",
    content: "公司主营业务收入规模连续增长。",
    documentTitle: "首次公开发行股票并在科创板上市招股说明书（注册稿）",
    sourcePage: 323,
    sourceQuote:
      "主营业务收入合计由2021年的16,088.21万元增至2023年的27,170.18万元。",
    sourceUrl: prospectusUrl,
  },
];

const profitabilityEvidence: Evidence[] = [
  {
    id: "EV-PRO-001",
    category: "financial",
    period: "2025 年上半年",
    content: "营业收入同比增长 17.70%，归母净利润同比增长 2.06%。",
    documentTitle: "思看科技2025年半年度报告",
    sourcePage: 8,
    sourceQuote:
      "营业收入176,848,509.44元，同比增长17.70%；归属于上市公司股东的净利润54,007,712.64元，同比增长2.06%。",
    sourceUrl: halfYearReportUrl,
  },
  {
    id: "EV-PRO-002",
    category: "financial",
    period: "2025 年上半年",
    content: "扣非归母净利润同比下降 2.93%，利润增速明显低于收入增速。",
    documentTitle: "思看科技2025年半年度报告",
    sourcePage: 8,
    sourceQuote:
      "归属于上市公司股东的扣除非经常性损益的净利润47,074,322.51元，同比下降2.93%。",
    sourceUrl: halfYearReportUrl,
  },
  {
    id: "EV-PRO-003",
    category: "financial",
    period: "2025 年上半年",
    content: "经营活动现金流净额同比下降 32.59%。",
    documentTitle: "思看科技2025年半年度报告",
    sourcePage: 9,
    sourceQuote:
      "经营活动产生的现金流量净额为3,115.91万元，同比下降32.59%，主要系产品迭代速度加快、备货增加导致购买材料支付的现金增加。",
    sourceUrl: halfYearReportUrl,
  },
];

const riskEvidence: Evidence[] = [
  {
    id: "EV-RISK-001",
    category: "business",
    period: "2025 年上半年",
    content: "产品结构变化可能对销售毛利率产生不利影响。",
    documentTitle: "思看科技2025年半年度报告",
    sourcePage: 43,
    sourceQuote:
      "如果公司未来的产品销售结构中，毛利率较低的产品的销售占比明显上升，则公司销售毛利率将受到不利影响。",
    sourceUrl: halfYearReportUrl,
  },
  {
    id: "EV-RISK-002",
    category: "business",
    period: "2025 年上半年",
    content: "技术优势减弱可能影响产品售价和市场占有率。",
    documentTitle: "思看科技2025年半年度报告",
    sourcePage: 43,
    sourceQuote:
      "如果公司未来产品技术优势减弱或消除，与竞争对手的优势不明显，则公司产品的销售价格和市场占有率将受到不利影响。",
    sourceUrl: halfYearReportUrl,
  },
  {
    id: "EV-RISK-003",
    category: "business",
    period: "2025 年上半年",
    content: "下游重要应用领域需求萎缩可能导致收入下降。",
    documentTitle: "思看科技2025年半年度报告",
    sourcePage: 43,
    sourceQuote:
      "如果航空航天、汽车制造、工程机械、交通运输等下游重要应用领域市场需求萎缩，则可能导致公司收入下降。",
    sourceUrl: halfYearReportUrl,
  },
];

const structureResponse: Omit<AskResponse, "sourceMode"> = {
  answer:
    "现有证据显示，思看科技的收入结构正在从较依赖便携式 3D 扫描仪，转向更多元的三维视觉数字化产品组合。2021—2023 年，便携式扫描仪收入仍在增长，但占主营业务收入的比例由 78.19% 降至 57.87%；同期，跟踪式产品占比由 11.77% 升至 26.58%。这更像是其他产品线增长更快，而不是核心产品萎缩。",
  claims: [
    { id: "CL-STR-001", text: "便携式 3D 扫描仪收入增长，但收入占比持续下降。", evidenceIds: ["EV-STR-001"] },
    { id: "CL-STR-002", text: "跟踪式 3D 视觉数字化产品正在成为更重要的收入来源。", evidenceIds: ["EV-STR-002"] },
  ],
  signals: [
    {
      id: "SIG-STR-001",
      type: "divergence",
      title: "核心产品收入增长，但依赖度下降",
      severity: "positive",
      description: "便携式扫描仪收入向上、占比向下，说明其他产品线增长速度更快，产品结构正在多元化。",
      evidenceIds: ["EV-STR-001", "EV-STR-002"],
    },
  ],
  charts: [
    {
      id: "CHART-STR-001",
      type: "line",
      title: "两类产品收入占比变化",
      subtitle: "便携式扫描仪占比下降，跟踪式产品占比持续上升",
      unit: "%",
      periods: ["2021", "2022", "2023"],
      series: [
        { name: "便携式 3D 扫描仪", values: [78.19, 68.87, 57.87] },
        { name: "跟踪式 3D 视觉数字化产品", values: [11.77, 18.01, 26.58] },
      ],
      evidenceIds: ["EV-STR-001", "EV-STR-002"],
    },
  ],
  evidence: structureEvidence,
  suggestedQuestions: demoSuggestedQuestions,
};

const profitabilityResponse: Omit<AskResponse, "sourceMode"> = {
  answer:
    "是的，思看科技在 2025 年上半年仍然盈利：归母净利润为 5,400.77 万元，同比增长 2.06%。但盈利质量有两个值得注意的变化：营收增长 17.70%，明显快于净利润；扣非归母净利润下降 2.93%，经营现金流净额下降 32.59%。报告解释称，利润增速受研发投入和海外销售网络建设影响，现金流下降则与产品迭代加快、备货增加有关。因此，更准确的结论是“仍在赚钱，但利润增速和现金回收弱于收入增长”。",
  claims: [
    { id: "CL-PRO-001", text: "2025年上半年归母净利润为5,400.77万元，同比增长2.06%。", evidenceIds: ["EV-PRO-001"] },
    { id: "CL-PRO-002", text: "扣非归母净利润同比下降2.93%。", evidenceIds: ["EV-PRO-002"] },
    { id: "CL-PRO-003", text: "经营活动现金流净额同比下降32.59%。", evidenceIds: ["EV-PRO-003"] },
  ],
  signals: [
    {
      id: "SIG-PRO-001",
      type: "divergence",
      title: "收入增长，但利润和现金流增长未同步",
      severity: "attention",
      description: "营收保持两位数增长，但净利润仅小幅增长，扣非利润与经营现金流下降，需继续观察投入转化和现金回收。",
      evidenceIds: ["EV-PRO-001", "EV-PRO-002", "EV-PRO-003"],
    },
  ],
  charts: [
    {
      id: "CHART-PRO-001",
      type: "line",
      title: "2025年上半年关键指标同比变化",
      subtitle: "收入增长与扣非利润、经营现金流出现分化",
      unit: "%",
      periods: ["营业收入", "归母净利润", "扣非净利润", "经营现金流"],
      series: [{ name: "同比变化", values: [17.7, 2.06, -2.93, -32.59] }],
      evidenceIds: ["EV-PRO-001", "EV-PRO-002", "EV-PRO-003"],
    },
  ],
  evidence: profitabilityEvidence,
  suggestedQuestions: demoSuggestedQuestions,
};

const riskResponse: Omit<AskResponse, "sourceMode"> = {
  answer:
    "根据目前掌握的信息，公司没有披露已经发生、足以对生产经营构成实质影响的重大风险。更值得持续关注的是技术差异化能否维持，以及产品结构和下游需求变化是否压低毛利率或收入。公司明确提示：低毛利率产品占比上升会影响整体毛利率；技术优势减弱会影响售价和市场份额；航空航天、汽车制造等下游需求收缩可能拖累收入。这些是风险提示，不代表问题已经发生。",
  claims: [
    { id: "CL-RISK-001", text: "低毛利率产品占比上升可能拖累整体毛利率。", evidenceIds: ["EV-RISK-001"] },
    { id: "CL-RISK-002", text: "技术优势减弱可能影响售价和市场占有率。", evidenceIds: ["EV-RISK-002"] },
    { id: "CL-RISK-003", text: "重要下游行业需求下降可能导致公司收入下降。", evidenceIds: ["EV-RISK-003"] },
  ],
  signals: [
    {
      id: "SIG-RISK-001",
      type: "attention",
      title: "增长依赖持续技术领先与下游需求",
      severity: "attention",
      description: "公司当前未披露已发生的重大经营风险，但技术差异化、产品毛利结构和下游景气度是需要继续验证的变量。",
      evidenceIds: ["EV-RISK-001", "EV-RISK-002", "EV-RISK-003"],
    },
  ],
  charts: [],
  evidence: riskEvidence,
  suggestedQuestions: demoSuggestedQuestions,
};

const insufficientResponse: Omit<AskResponse, "sourceMode"> = {
  answer: "根据目前掌握的信息，我无法可靠回答这个问题。",
  claims: [],
  signals: [],
  charts: [],
  evidence: [],
  suggestedQuestions: demoSuggestedQuestions,
};

function selectResponse(question: string) {
  if (["赚钱", "盈利", "利润", "现金流"].some((keyword) => question.includes(keyword))) {
    return profitabilityResponse;
  }
  if (["风险", "注意", "关注"].some((keyword) => question.includes(keyword))) {
    return riskResponse;
  }
  if (["收入结构", "产品线", "扫描仪", "多元"].some((keyword) => question.includes(keyword))) {
    return structureResponse;
  }
  return insufficientResponse;
}

export async function mockAsk(
  question: string,
  delayMs = 2900,
  sourceMode: AskResponse["sourceMode"] = "mock",
): Promise<AskResponse> {
  if (delayMs > 0) {
    await new Promise((resolve) => window.setTimeout(resolve, delayMs));
  }
  return { sourceMode, ...selectResponse(question) };
}
