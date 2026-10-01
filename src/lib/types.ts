export type Evidence = {
  id: string;
  category: "financial" | "business" | "company";
  period: string;
  content: string;
  documentTitle: string;
  sourcePage?: number;
  sourceQuote: string;
  sourceUrl: string;
};

export type Claim = {
  id: string;
  text: string;
  evidenceIds: string[];
};

export type Signal = {
  id: string;
  type: "divergence" | "trend" | "attention";
  title: string;
  severity: "attention" | "positive";
  description: string;
  evidenceIds: string[];
};

export type ChartSpec = {
  id: string;
  type: "line";
  title: string;
  subtitle: string;
  unit: string;
  periods: string[];
  series: Array<{ name: string; values: number[] }>;
  evidenceIds: string[];
};

export type AskResponse = {
  answer: string;
  claims: Claim[];
  signals: Signal[];
  charts: ChartSpec[];
  evidence: Evidence[];
  suggestedQuestions: string[];
};
