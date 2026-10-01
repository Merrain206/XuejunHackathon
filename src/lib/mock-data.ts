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

export const evidence: Evidence[] = [
  {
    id: "EV-001",
    category: "business",
    period: "2021—2023",
    content: "便携式 3D 扫描仪收入持续增长，但在主营业务收入中的占比连续下降。",
    documentTitle: "首次公开发行股票并在科创板上市招股说明书（注册稿）",
    sourcePage: 323,
    sourceQuote:
      "便携式3D扫描仪销售收入由12,579.02万元增至15,722.33万元，占主营业务收入比例由78.19%降至57.87%。",
    sourceUrl:
      "https://static.sse.com.cn/stock/disclosure/announcement/c/202408/001845_20240816_R2YE.pdf",
  },
  {
    id: "EV-002",
    category: "business",
    period: "2021—2023",
    content: "跟踪式 3D 视觉数字化产品收入和收入占比快速提升。",
    documentTitle: "首次公开发行股票并在科创板上市招股说明书（注册稿）",
    sourcePage: 323,
    sourceQuote:
      "跟踪式3D视觉数字化产品销售收入由1,893.69万元增至7,222.66万元，占比由11.77%升至26.58%。",
    sourceUrl:
      "https://static.sse.com.cn/stock/disclosure/announcement/c/202408/001845_20240816_R2YE.pdf",
  },
  {
    id: "EV-003",
    category: "financial",
    period: "2021—2023",
    content: "公司主营业务收入规模连续增长。",
    documentTitle: "首次公开发行股票并在科创板上市招股说明书（注册稿）",
    sourcePage: 323,
    sourceQuote:
      "主营业务收入合计由2021年的16,088.21万元增至2023年的27,170.18万元。",
    sourceUrl:
      "https://static.sse.com.cn/stock/disclosure/announcement/c/202408/001845_20240816_R2YE.pdf",
  },
];

const supportedResponse: AskResponse = {
  answer:
    "现有证据显示，思看科技的收入结构正在从单一的便携式 3D 扫描仪，转向更多元的三维视觉数字化产品组合。2021—2023 年，便携式 3D 扫描仪收入仍在增长，但占主营业务收入的比例由 78.19% 降至 57.87%；同期，跟踪式产品占比由 11.77% 升至 26.58%。这更像是产品组合扩张，而不是核心产品萎缩。",
  claims: [
    {
      id: "CL-001",
      text: "便携式 3D 扫描仪收入增长，但收入占比持续下降。",
      evidenceIds: ["EV-001"],
    },
    {
      id: "CL-002",
      text: "跟踪式 3D 视觉数字化产品正在成为更重要的收入来源。",
      evidenceIds: ["EV-002"],
    },
  ],
  signals: [
    {
      id: "SIG-001",
      type: "divergence",
      title: "核心产品收入增长，但依赖度下降",
      severity: "positive",
      description:
        "便携式扫描仪收入向上、占比向下，说明新增产品线的增长速度更快，产品结构正在多元化。",
      evidenceIds: ["EV-001", "EV-002"],
    },
  ],
  charts: [
    {
      id: "CHART-001",
      type: "line",
      title: "两类产品收入占比变化",
      subtitle: "便携式扫描仪占比下降，跟踪式产品占比持续上升",
      unit: "%",
      periods: ["2021", "2022", "2023"],
      series: [
        { name: "便携式 3D 扫描仪", values: [78.19, 68.87, 57.87] },
        { name: "跟踪式 3D 视觉数字化产品", values: [11.77, 18.01, 26.58] },
      ],
      evidenceIds: ["EV-001", "EV-002"],
    },
  ],
  evidence,
  suggestedQuestions: [
    "哪个产品线增长最快？",
    "这种产品结构变化意味着什么？",
    "收入增长是否过度依赖单一客户？",
  ],
};

const insufficientResponse: AskResponse = {
  answer: "根据目前掌握的信息，我无法可靠回答这个问题。",
  claims: [],
  signals: [],
  charts: [],
  evidence: [],
  suggestedQuestions: ["你的收入结构发生了什么变化？", "哪个产品线增长最快？"],
};

export async function mockAsk(question: string): Promise<AskResponse> {
  await new Promise((resolve) => window.setTimeout(resolve, 900));

  const supportedKeywords = ["产品", "扫描仪", "结构", "变化", "最快", "多元", "信号"];
  return supportedKeywords.some((keyword) => question.includes(keyword))
    ? supportedResponse
    : insufficientResponse;
}
