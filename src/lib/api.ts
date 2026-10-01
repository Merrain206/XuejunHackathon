import { mockAsk } from "./mock-data";
import type { AskResponse } from "./types";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");

type AskApiResponse = {
  answer: string;
  claims: Array<{ id: string; text: string; evidence_ids: string[] }>;
  signals: Array<{
    id: string;
    type: "divergence" | "trend" | "attention";
    title: string;
    severity: "attention" | "positive";
    description: string;
    evidence_ids: string[];
  }>;
  charts?: Array<{
    id: string;
    type: "line";
    title: string;
    subtitle?: string;
    unit: string;
    periods: string[];
    series: Array<{ name: string; values: number[] }>;
    evidence_ids: string[];
  }>;
  evidence: Array<{
    id: string;
    category: "financial" | "business" | "company";
    period: string;
    content: string;
    document_title?: string;
    document_id: string;
    source_page?: number;
    source_quote: string;
    source_url?: string;
  }>;
  suggested_questions: string[];
};

export async function askCompany(companyId: string, question: string): Promise<AskResponse> {
  if (!apiBaseUrl) {
    return mockAsk(question);
  }

  const response = await fetch(`${apiBaseUrl}/companies/${companyId}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });

  if (!response.ok) {
    throw new Error(`Ask API failed with status ${response.status}`);
  }

  const data = (await response.json()) as AskApiResponse;

  return {
    answer: data.answer,
    claims: data.claims.map((claim) => ({
      id: claim.id,
      text: claim.text,
      evidenceIds: claim.evidence_ids,
    })),
    signals: data.signals.map((signal) => ({
      id: signal.id,
      type: signal.type,
      title: signal.title,
      severity: signal.severity,
      description: signal.description,
      evidenceIds: signal.evidence_ids,
    })),
    charts: (data.charts ?? []).map((chart) => ({
      id: chart.id,
      type: chart.type,
      title: chart.title,
      subtitle: chart.subtitle ?? "",
      unit: chart.unit,
      periods: chart.periods,
      series: chart.series,
      evidenceIds: chart.evidence_ids,
    })),
    evidence: data.evidence.map((item) => ({
      id: item.id,
      category: item.category,
      period: item.period,
      content: item.content,
      documentTitle: item.document_title ?? item.document_id,
      sourcePage: item.source_page,
      sourceQuote: item.source_quote,
      sourceUrl: item.source_url ?? "#",
    })),
    suggestedQuestions: data.suggested_questions,
  };
}
