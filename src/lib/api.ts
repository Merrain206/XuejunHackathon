import { demoSuggestedQuestions, mockAsk } from "./mock-data";
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
    source_page: number;
    source_quote: string;
    source_url: string;
  }>;
  suggested_questions?: string[];
};

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => isNonEmptyString(item));
}

function isOptionalString(value: unknown): value is string | undefined {
  return value === undefined || typeof value === "string";
}

function isHttpUrl(value: unknown): value is string {
  if (!isNonEmptyString(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

function isClaim(value: unknown): value is AskApiResponse["claims"][number] {
  return isRecord(value)
    && isNonEmptyString(value.id)
    && isNonEmptyString(value.text)
    && isStringArray(value.evidence_ids)
    && value.evidence_ids.length > 0;
}

function isSignal(value: unknown): value is AskApiResponse["signals"][number] {
  return isRecord(value)
    && isNonEmptyString(value.id)
    && ["divergence", "trend", "attention"].includes(value.type as string)
    && isNonEmptyString(value.title)
    && ["attention", "positive"].includes(value.severity as string)
    && isNonEmptyString(value.description)
    && isStringArray(value.evidence_ids)
    && value.evidence_ids.length > 0;
}

function isChart(value: unknown): value is NonNullable<AskApiResponse["charts"]>[number] {
  if (!isRecord(value)) return false;
  const periods = value.periods;
  const series = value.series;

  if (!isNonEmptyString(value.id)
    || value.type !== "line"
    || !isNonEmptyString(value.title)
    || !isOptionalString(value.subtitle)
    || typeof value.unit !== "string"
    || !isStringArray(periods)
    || periods.length === 0
    || !Array.isArray(series)
    || series.length === 0
    || !isStringArray(value.evidence_ids)
    || value.evidence_ids.length === 0) {
    return false;
  }

  return series.every((item) => isRecord(item)
    && isNonEmptyString(item.name)
    && Array.isArray(item.values)
    && item.values.length === periods.length
    && item.values.every((seriesValue) => typeof seriesValue === "number" && Number.isFinite(seriesValue)));
}

function isEvidence(value: unknown): value is AskApiResponse["evidence"][number] {
  return isRecord(value)
    && isNonEmptyString(value.id)
    && ["financial", "business", "company"].includes(value.category as string)
    && isNonEmptyString(value.period)
    && isNonEmptyString(value.content)
    && isOptionalString(value.document_title)
    && isNonEmptyString(value.document_id)
    && typeof value.source_page === "number"
    && Number.isInteger(value.source_page)
    && value.source_page > 0
    && isNonEmptyString(value.source_quote)
    && isHttpUrl(value.source_url);
}

function isAskApiResponse(value: unknown): value is AskApiResponse {
  if (!isRecord(value)
    || !isNonEmptyString(value.answer)
    || !Array.isArray(value.claims)
    || !value.claims.every(isClaim)
    || !Array.isArray(value.signals)
    || !value.signals.every(isSignal)
    || (value.charts !== undefined && (!Array.isArray(value.charts) || !value.charts.every(isChart)))
    || !Array.isArray(value.evidence)
    || !value.evidence.every(isEvidence)
    || (value.suggested_questions !== undefined && !isStringArray(value.suggested_questions))) {
    return false;
  }

  const evidenceIds = new Set(value.evidence.map((item) => item.id));
  if (evidenceIds.size !== value.evidence.length) return false;

  const references = [
    ...value.claims.map((item) => item.evidence_ids),
    ...value.signals.map((item) => item.evidence_ids),
    ...(value.charts ?? []).map((item) => item.evidence_ids),
  ];
  return references.every((ids) => ids.every((id) => evidenceIds.has(id)));
}

export async function askCompany(companyId: string, question: string): Promise<AskResponse> {
  if (!apiBaseUrl) {
    return mockAsk(question);
  }

  try {
    const response = await fetch(`${apiBaseUrl}/companies/${companyId}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
      signal: AbortSignal.timeout(8000),
    });

    if (!response.ok) {
      throw new Error(`Ask API failed with status ${response.status}`);
    }

    const data: unknown = await response.json();
    if (!isAskApiResponse(data)) {
      throw new Error("Ask API returned an invalid response");
    }

    return {
      sourceMode: "api",
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
        sourceUrl: item.source_url,
      })),
      suggestedQuestions: demoSuggestedQuestions,
    };
  } catch {
    return mockAsk(question, 0, "fallback");
  }
}
