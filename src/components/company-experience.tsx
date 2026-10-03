"use client";

import Link from "next/link";
import { FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { askCompany } from "@/lib/api";
import { companies, type Company } from "@/lib/companies";
import type { AskResponse, Evidence } from "@/lib/types";
import { EvidenceChart } from "./evidence-chart";

const loadingSteps = [
  { title: "正在理解用户问题", detail: "识别查询对象与信息范围" },
  { title: "正在搜索数据库", detail: "查找与问题相关的原始资料" },
  { title: "正在整理证据", detail: "核对来源、页码和引用关系" },
  { title: "正在判断是否需要图表", detail: "检查数据是否适合可视化" },
  { title: "正在总结回答", detail: "仅保留有证据支持的结论" },
];

type ReliabilityLevel = "high" | "medium" | "pending";
type ReliabilityReasonState = "verified" | "neutral" | "warning";

const materialNumberPattern = /[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:%|亿元|万元|元)|\d+\.\d+)/g;

function parseMaterialNumber(value: string) {
  const numericValue = Number.parseFloat(value.replaceAll(",", ""));
  if (!Number.isFinite(numericValue)) return null;
  if (value.includes("%")) return { kind: "percent", value: numericValue };
  if (value.includes("亿元")) return { kind: "amount", value: numericValue * 100_000_000 };
  if (value.includes("万元")) return { kind: "amount", value: numericValue * 10_000 };
  if (value.includes("元")) return { kind: "amount", value: numericValue };
  return { kind: "bare", value: numericValue };
}

function valuesMatch(left: string, right: string) {
  const parsedLeft = parseMaterialNumber(left);
  const parsedRight = parseMaterialNumber(right);
  if (!parsedLeft || !parsedRight) return false;
  if (parsedLeft.kind !== parsedRight.kind && parsedLeft.kind !== "bare" && parsedRight.kind !== "bare") return false;
  if (parsedLeft.kind === "percent") return Math.abs(parsedLeft.value - parsedRight.value) <= 0.005;
  const scale = Math.max(Math.abs(parsedLeft.value), Math.abs(parsedRight.value), 1);
  return Math.abs(parsedLeft.value - parsedRight.value) / scale <= 0.00001;
}

function findSupportingEvidence(value: string, evidence: Evidence[]) {
  return evidence.find((item) => {
    const sourceValues = item.sourceQuote.match(materialNumberPattern) ?? [];
    return sourceValues.some((sourceValue) => valuesMatch(value, sourceValue));
  });
}

function EvidenceRichText({ text, evidence, onEvidence }: { text: string; evidence: Evidence[]; onEvidence: (item: Evidence) => void }) {
  const matches = Array.from(text.matchAll(materialNumberPattern));
  if (!matches.length) return text;

  const content: ReactNode[] = [];
  let cursor = 0;
  matches.forEach((match, index) => {
    const start = match.index ?? 0;
    const value = match[0];
    const item = findSupportingEvidence(value, evidence);
    content.push(text.slice(cursor, start));
    content.push(item ? <button className="number-highlight" key={`${start}-${value}`} type="button" onClick={() => onEvidence(item)} title={`查看支持 ${value.trim()} 的证据：${item.id}`}>{value}</button> : value);
    cursor = start + value.length;
    if (index === matches.length - 1) content.push(text.slice(cursor));
  });
  return content;
}

function FormattedSourceQuote({ text }: { text: string }) {
  const normalized = text.trim().replace(/\s+/g, " ");
  const matches = Array.from(normalized.matchAll(materialNumberPattern));
  const gaps = matches.slice(1).map((match, index) => {
    const previous = matches[index];
    return normalized.slice((previous.index ?? 0) + previous[0].length, match.index ?? 0);
  });
  const suffix = matches.length
    ? normalized.slice((matches.at(-1)?.index ?? 0) + (matches.at(-1)?.[0].length ?? 0))
    : "";
  const canUseTableLayout = matches.length >= 2
    && gaps.every((gap) => !gap.trim())
    && !suffix.trim();

  if (canUseTableLayout) {
    const label = normalized.slice(0, matches[0].index ?? 0).trim();
    return (
      <blockquote aria-label={`原文摘录：${normalized}`} className="mt-4 border-l-2 border-teal-600 pl-4 text-slate-700">
        {label ? <p className="text-sm font-medium leading-6 text-slate-800">“{label}</p> : <span>“</span>}
        <div className="mt-3 flex flex-wrap gap-2">
          {matches.map((match, index) => <span className="rounded-sm border border-slate-200 bg-slate-50 px-2.5 py-1.5 font-mono text-[13px] text-slate-700" key={`${match.index}-${match[0]}`}>{match[0]}{index === matches.length - 1 ? "”" : ""}</span>)}
        </div>
      </blockquote>
    );
  }

  return <blockquote className="mt-4 whitespace-pre-line border-l-2 border-teal-600 pl-4 text-[15px] leading-7 text-slate-700">“{normalized}”</blockquote>;
}

function insufficientReason(sourceMode: AskResponse["sourceMode"], companyId: string) {
  if (sourceMode === "unavailable") return "可能原因：动态证据服务当前不可用。请恢复后端服务后重试。";
  if (companyId === "688583") return "可能原因：当前已核验的演示资料没有覆盖这个问题。你可以改问收入结构、盈利质量或主要风险。";
  return "可能原因：公开披露没有覆盖这个问题，或当前证据与模型输出未达到逐条引用的校验要求。可以尝试补充具体指标或报告期后重试。";
}

function getEvidenceReliability(item: Evidence, companyId: string): {
  level: ReliabilityLevel;
  label: string;
  reasons: { state: ReliabilityReasonState; text: string }[];
} {
  let isOfficialSource = false;
  try {
    const sourceUrl = new URL(item.sourceUrl);
    isOfficialSource = sourceUrl.protocol === "https:" && ["sse.com.cn", "cninfo.com.cn"].some(
      (domain) => sourceUrl.hostname === domain || sourceUrl.hostname.endsWith(`.${domain}`),
    );
  } catch {
    isOfficialSource = false;
  }

  const hasPrecisePage = typeof item.sourcePage === "number" && Number.isInteger(item.sourcePage) && item.sourcePage > 0;
  const hasSourceQuote = item.sourceQuote.trim().length >= 10;
  const contentValues = item.content.match(materialNumberPattern) ?? [];
  const numericMatch = contentValues.length
    ? contentValues.every((value) => findSupportingEvidence(value, [item]))
    : null;

  const evidenceChainComplete = isOfficialSource && hasPrecisePage && hasSourceQuote && numericMatch !== false;
  const level: ReliabilityLevel = item.verificationStatus === "pending" || !evidenceChainComplete
    ? "pending"
    : item.verificationStatus === "verified"
      ? "high"
      : item.verificationStatus === "auto" || companyId !== "688583"
        ? "medium"
        : "high";

  const verificationReason = item.verificationStatus === "verified"
    ? { state: "verified" as const, text: "后端标记为已核验 Evidence" }
    : item.verificationStatus === "auto"
      ? { state: "neutral" as const, text: "原文与页码已由后端机械核验，未标记人工复核" }
      : item.verificationStatus === "pending"
        ? { state: "warning" as const, text: "后端标记为待核验 Evidence" }
        : companyId === "688583"
          ? { state: "verified" as const, text: "思看科技演示证据已人工核验" }
          : { state: "neutral" as const, text: "动态 Evidence 未标记人工核验，最高按中可靠度处理" };

  return {
    level,
    label: level === "high" ? "高" : level === "medium" ? "中" : "待核验",
    reasons: [
      { state: isOfficialSource ? "verified" : "warning", text: isOfficialSource ? "来自上交所或巨潮资讯官方披露地址" : "来源不是已识别的官方披露地址" },
      { state: hasPrecisePage ? "verified" : "warning", text: hasPrecisePage ? `精确定位至 PDF 第 ${String(item.sourcePage)} 页` : "缺少可核查的 PDF 页码" },
      { state: hasSourceQuote ? "verified" : "warning", text: hasSourceQuote ? "保留可核对的原文摘录" : "缺少可核对的原文摘录" },
      {
        state: numericMatch === null ? "neutral" : numericMatch ? "verified" : "warning",
        text: numericMatch === null ? "该证据摘要不含需逐项核对的金额或比例" : numericMatch ? "摘要中的关键数字与原文一致" : "摘要中的关键数字未全部在原文中匹配",
      },
      verificationReason,
    ],
  };
}

function ArrowIcon({ direction = "right" }: { direction?: "right" | "up" }) {
  return (
    <svg aria-hidden="true" className={direction === "up" ? "size-4 -rotate-45" : "size-4"} viewBox="0 0 20 20" fill="none">
      <path d="M4 10h12m-5-5 5 5-5 5" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

function ScanMark() {
  return (
    <div className="scan-mark" aria-hidden="true">
      <span /><span /><span /><span />
    </div>
  );
}

function EvidenceDrawer({ item, companyId, onClose }: { item: Evidence; companyId: string; onClose: () => void }) {
  const reliability = getEvidenceReliability(item, companyId);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/30 backdrop-blur-[2px]">
      <button aria-label="关闭证据详情" className="absolute inset-0 cursor-default" onClick={onClose} />
      <aside className="relative z-10 flex h-full w-full max-w-xl flex-col border-l border-slate-200 bg-[#fbfcfa] shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-5 sm:px-8">
          <div>
            <p className="eyebrow">Evidence detail</p>
            <h2 className="mt-1 text-lg font-semibold text-slate-950">证据详情</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="关闭">×</button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-7 sm:px-8">
          <div className="flex items-center gap-2 text-xs font-medium text-teal-800">
            <span className="rounded-full bg-teal-50 px-2.5 py-1">{item.category.toUpperCase()}</span>
            <span>{item.id}</span>
          </div>
          <h3 className="mt-6 text-2xl font-semibold leading-snug tracking-tight text-slate-950">{item.content}</h3>

          <dl className="mt-8 grid grid-cols-[92px_1fr] gap-x-4 gap-y-4 border-y border-slate-200 py-6 text-sm">
            <dt className="text-slate-500">报告期</dt><dd className="font-medium text-slate-900">{item.period}</dd>
            <dt className="text-slate-500">来源</dt><dd className="font-medium leading-6 text-slate-900">{item.documentTitle}</dd>
            <dt className="text-slate-500">页码</dt><dd className="font-mono font-medium text-slate-900">{item.sourcePage ? `P. ${item.sourcePage}` : "未提供"}</dd>
          </dl>

          <section className="reliability-panel mt-7" aria-labelledby="reliability-heading">
            <div className="flex items-center justify-between gap-4">
              <div><p className="eyebrow">Evidence reliability</p><h4 className="mt-1 text-sm font-semibold text-slate-900" id="reliability-heading">为什么是这个可靠度</h4></div>
              <span className={`reliability-badge ${reliability.level}`}>可靠度 · {reliability.label}</span>
            </div>
            <ul className="mt-4 space-y-2.5">
              {reliability.reasons.map((reason) => <li className={`reliability-reason ${reason.state}`} key={reason.text}><span aria-hidden="true">{reason.state === "verified" ? "✓" : reason.state === "warning" ? "!" : "—"}</span><p>{reason.text}</p></li>)}
            </ul>
            <p className="mt-4 border-t border-slate-200 pt-3 text-[11px] leading-5 text-slate-500">可靠度反映来源与引用链的完整性，不代表公司披露内容已被独立验证。</p>
          </section>

          <div className="mt-7 rounded-sm border border-slate-200 bg-white p-5">
            <p className="eyebrow">Original excerpt</p>
            <FormattedSourceQuote text={item.sourceQuote} />
            <p className="mt-3 text-[11px] leading-5 text-slate-400">仅调整空格、换行和数字分组以便阅读，未改动原文字词与披露数值。</p>
          </div>

          <a className="mt-6 flex items-center justify-between border-b border-slate-950 pb-3 text-sm font-semibold text-slate-950 transition-colors hover:text-teal-700" href={`${item.sourceUrl}${item.sourcePage ? `#page=${item.sourcePage}` : ""}`} target="_blank" rel="noreferrer">
            打开官方原始文件 <ArrowIcon direction="up" />
          </a>
        </div>
      </aside>
    </div>
  );
}

function SignalCard({ response, onEvidence }: { response: AskResponse; onEvidence: (item: Evidence) => void }) {
  const signal = response.signals[0];
  if (!signal) return null;

  return (
    <section className="signal-card animate-rise">
      <div className="flex gap-4">
        <div className="signal-pulse mt-1" />
        <div className="min-w-0 flex-1">
          <p className="eyebrow text-amber-700">X-Ray signal · {signal.type}</p>
          <h3 className="mt-2 text-xl font-semibold tracking-tight text-slate-950">{signal.title}</h3>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">{signal.description}</p>
          <div className="mt-5 flex flex-wrap gap-2">
            {signal.evidenceIds.map((id) => {
              const item = response.evidence.find((entry) => entry.id === id);
              return item ? <button className="evidence-chip" key={id} onClick={() => onEvidence(item)}>{id}<ArrowIcon /></button> : null;
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

export function CompanyExperience({ company }: { company: Company }) {
  const [question, setQuestion] = useState("");
  const [askedQuestion, setAskedQuestion] = useState("");
  const [response, setResponse] = useState<AskResponse | null>(null);
  const [dataMode, setDataMode] = useState<AskResponse["sourceMode"] | "pending">("pending");
  const [loading, setLoading] = useState(false);
  const [loadingStage, setLoadingStage] = useState(0);
  const [error, setError] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);
  const activeRequest = useRef<{ id: number; controller: AbortController } | null>(null);
  const nextRequestId = useRef(0);

  useEffect(() => {
    setQuestion("");
    setAskedQuestion("");
    setResponse(null);
    setDataMode("pending");
    setLoading(false);
    setError("");
    setSelectedEvidence(null);
    activeRequest.current?.controller.abort();
    activeRequest.current = null;

    return () => {
      activeRequest.current?.controller.abort();
      activeRequest.current = null;
    };
  }, [company.id]);

  useEffect(() => {
    if (!loading) return;

    setLoadingStage(0);
    const interval = window.setInterval(() => {
      setLoadingStage((current) => Math.min(current + 1, loadingSteps.length - 1));
    }, 560);

    return () => window.clearInterval(interval);
  }, [loading]);

  async function ask(nextQuestion?: string) {
    const prompt = (nextQuestion ?? question).trim();
    if (!prompt || loading) return;
    setQuestion(prompt);
    setAskedQuestion(prompt);
    setLoading(true);
    setError("");
    setResponse(null);
    setSelectedEvidence(null);
    activeRequest.current?.controller.abort();
    const controller = new AbortController();
    const requestId = ++nextRequestId.current;
    activeRequest.current = { id: requestId, controller };
    try {
      const result = await askCompany(company.id, prompt, controller.signal);
      if (activeRequest.current?.id !== requestId) return;
      setDataMode(result.sourceMode);
      setResponse(result);
    } catch {
      if (controller.signal.aborted || activeRequest.current?.id !== requestId) return;
      setError("这次检索没有完成，请稍后重试。");
    } finally {
      if (activeRequest.current?.id === requestId) {
        activeRequest.current = null;
        setLoading(false);
      }
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    void ask();
  }

  return (
    <main className="app-shell min-h-screen text-slate-950">
      <header className="border-b border-slate-200/90 bg-[#f8faf7]">
        <div className="mx-auto flex h-16 max-w-[1440px] items-center justify-between px-5 sm:px-8 lg:px-12">
          <div className="flex items-center gap-3"><ScanMark /><div><p className="text-sm font-semibold tracking-tight">ASK THE COMPANY</p><p className="text-[10px] uppercase tracking-[0.18em] text-slate-400">Evidence intelligence</p></div></div>
          <div className={`source-badge ${dataMode}`}><span className="status-dot" />{dataMode === "api" ? "LIVE API" : dataMode === "fallback" ? "DEMO FALLBACK" : dataMode === "mock" ? "DEMO DATA" : dataMode === "unavailable" ? "SERVICE UNAVAILABLE" : "等待首次提问"}</div>
        </div>
      </header>

      <div className="mx-auto grid max-w-[1440px] lg:grid-cols-[310px_minmax(0,1fr)]">
        <aside className="border-b border-slate-200 bg-[#eef1ed] px-5 py-7 sm:px-8 lg:min-h-[calc(100vh-64px)] lg:border-b-0 lg:border-r lg:px-8 lg:py-10">
          <nav aria-label="切换公司" className="company-switcher">
            {companies.map((item) => <Link aria-current={item.id === company.id ? "page" : undefined} className={`company-switcher-link ${item.id === company.id ? "is-active" : ""}`} href={`/company/${item.id}`} key={item.id}><span>{item.name}</span><span>{item.ticker}</span></Link>)}
          </nav>
          <p className="eyebrow">Company dossier</p>
          <div className="mt-5 flex items-start gap-4"><div className="company-monogram">{company.monogram}</div><div><h1 className="text-2xl font-semibold tracking-tight">{company.name}</h1><p className="mt-1 font-mono text-xs text-slate-500">{company.exchangeCode} · {company.ticker}</p></div></div>
          <div className="mt-8 flex flex-wrap gap-2"><span className="tag">{company.exchange}</span><span className="tag">已上市</span></div>

          <dl className="mt-8 space-y-5 border-t border-slate-300 pt-6 text-sm">
            <div><dt className="text-xs text-slate-500">公司全称</dt><dd className="mt-1.5 leading-6 text-slate-800">{company.fullName}</dd></div>
            <div><dt className="text-xs text-slate-500">主营领域</dt><dd className="mt-1.5 leading-6 text-slate-800">{company.industry}</dd></div>
            {company.listedAt ? <div><dt className="text-xs text-slate-500">上市日期</dt><dd className="mt-1.5 font-mono text-slate-800">{company.listedAt}</dd></div> : null}
          </dl>

          <div className="mt-8 border-t border-slate-300 pt-6">
            <p className="eyebrow">Source coverage</p>
            <div className="mt-4 flex items-center justify-between text-sm"><span className="text-slate-600">公开文件</span><span className="font-mono font-semibold">{company.id === "688583" ? "02" : "READY"}</span></div>
            <div className="mt-3 h-1 overflow-hidden bg-slate-300"><div className={`h-full bg-teal-700 ${company.id === "688583" ? "w-2/3" : "w-full"}`} /></div>
            <p className="mt-3 text-xs leading-5 text-slate-500">{company.id === "688583" ? "当前演示使用已核验的招股书与 2025 年半年度报告。" : "公司公告已收录，可通过动态 Evidence 服务检索并核验引用。"}</p>
          </div>
        </aside>

        <div className="min-w-0 px-5 py-8 sm:px-8 lg:px-12 lg:py-10 xl:px-16">
          <section className="border-b border-slate-200 pb-9">
            <div className="flex flex-col justify-between gap-5 xl:flex-row xl:items-end">
              <div><p className="eyebrow">Ask · verify · understand</p><h2 className="mt-3 max-w-3xl text-3xl font-semibold leading-tight tracking-[-0.03em] sm:text-4xl">不要阅读一家公司。<br />直接问它。</h2></div>
              <p className="max-w-sm text-sm leading-6 text-slate-500">回答只来自可追溯证据。每个事实、判断与信号，都可以回到原始文件。</p>
            </div>

            <form className="ask-box mt-8" onSubmit={handleSubmit}>
              <div className="flex items-center gap-3 border-b border-slate-200 px-5 py-3 text-xs text-slate-400"><ScanMark />正在向 {company.name} 提问</div>
              <div className="flex items-end gap-3 p-3 sm:p-4">
                <textarea aria-label="向公司提问" className="min-h-16 flex-1 resize-none bg-transparent px-2 py-2 text-base leading-7 outline-none placeholder:text-slate-400 sm:text-lg" placeholder="例如：你的收入结构发生了什么变化？" value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void ask(); } }} />
                <button className={`ask-button ${loading ? "is-loading" : ""}`} type="submit" disabled={!question.trim() || loading}>{loading ? "检索中" : "提问"}<ArrowIcon /></button>
              </div>
            </form>

            {!response && !loading ? <div className="mt-4 flex flex-wrap gap-2">{company.suggestedQuestions.map((item) => <button className="question-chip" key={item} onClick={() => void ask(item)} type="button">{item}</button>)}</div> : null}
          </section>

          <section className="py-8">
            {!response && !loading && !error ? <div className="grid gap-3 sm:grid-cols-3">{company.metrics.map((metric) => <div className="metric-card" key={metric.label}><p className="text-xs text-slate-500">{metric.label}</p><p className="mt-4 text-2xl font-semibold tracking-tight">{metric.value}</p><p className="mt-1 font-mono text-[10px] uppercase tracking-wider text-slate-400">{metric.note}</p></div>)}</div> : null}

            {loading ? <div className="loading-panel" role="status" aria-live="polite">
              <div className="scanner-line" />
              <div className="flex items-start gap-4">
                <div className="loading-ring mt-0.5" />
                <div className="min-w-0 flex-1">
                  <p className="eyebrow">Investigation progress</p>
                  <p className="loading-title mt-2 text-base font-semibold">{loadingSteps[loadingStage].title}</p>
                  <p className="mt-1 text-xs text-slate-500">{loadingSteps[loadingStage].detail}</p>
                  <ol className="mt-6 grid gap-3 border-t border-slate-200 pt-5 sm:grid-cols-5">
                    {loadingSteps.map((step, index) => <li className={`progress-step ${index < loadingStage ? "is-done" : ""} ${index === loadingStage ? "is-active" : ""}`} key={step.title}>
                      <span className="progress-dot">{index < loadingStage ? "✓" : String(index + 1).padStart(2, "0")}</span>
                      <span>{step.title.replace("正在", "")}</span>
                    </li>)}
                  </ol>
                </div>
              </div>
            </div> : null}
            {error ? <div className="border border-red-200 bg-red-50 p-5 text-sm text-red-800">{error}</div> : null}

            {response ? <div className="response-stack space-y-5">
              {response.sourceMode === "fallback" ? <div className="fallback-notice animate-rise"><span>!</span><p>后端暂时不可用，当前展示已核验的演示数据。</p></div> : null}
              {response.sourceMode === "unavailable" ? <div className="unavailable-notice animate-rise"><span>!</span><p>动态证据服务暂时不可用，未使用其他公司的演示数据。</p></div> : null}
              <div className="animate-rise"><p className="eyebrow">Question</p><p className="mt-2 text-sm font-medium text-slate-700">“{askedQuestion}”</p></div>
              <SignalCard response={response} onEvidence={setSelectedEvidence} />

              <article className="answer-card animate-rise">
                <div className="flex items-center justify-between border-b border-slate-200 pb-4"><p className="eyebrow">Evidence-based answer</p><span className="verified-label"><span>{response.evidence.length ? "✓" : "—"}</span> {response.evidence.length ? "已验证" : "证据不足"}</span></div>
                <p className="mt-6 max-w-3xl text-[17px] leading-8 text-slate-800"><EvidenceRichText text={response.answer} evidence={response.evidence} onEvidence={setSelectedEvidence} /></p>
                {!response.evidence.length ? <div className="mt-5 max-w-3xl rounded-sm border border-slate-200 bg-slate-50 px-4 py-3"><p className="text-xs font-semibold text-slate-700">为什么没有给出结论</p><p className="mt-1 text-xs leading-5 text-slate-500">{insufficientReason(response.sourceMode, company.id)}</p></div> : null}
                {response.claims.length ? <div className="mt-7 border-t border-slate-200 pt-5"><p className="eyebrow">Key claims</p><div className="mt-3 space-y-3">{response.claims.map((claim, index) => <div className="flex gap-3 text-sm leading-6" key={claim.id}><span className="claim-index">{String(index + 1).padStart(2, "0")}</span><p className="flex-1 text-slate-700"><EvidenceRichText text={claim.text} evidence={response.evidence.filter((item) => claim.evidenceIds.includes(item.id))} onEvidence={setSelectedEvidence} /></p><div className="flex flex-wrap justify-end gap-1">{claim.evidenceIds.map((id) => { const item = response.evidence.find((entry) => entry.id === id); return item ? <button className="claim-evidence-button" key={id} onClick={() => setSelectedEvidence(item)}>{id}</button> : null; })}</div></div>)}</div></div> : null}
              </article>

              {response.charts.map((chart) => <EvidenceChart chart={chart} key={chart.id} />)}

              {response.evidence.length ? <section className="animate-rise"><div className="mb-3 flex items-center justify-between"><p className="eyebrow">Evidence trail</p><span className="font-mono text-[10px] text-slate-400">{response.evidence.length} ITEMS</span></div><div className="grid gap-3 xl:grid-cols-3">{response.evidence.map((item) => { const reliability = getEvidenceReliability(item, company.id); return <button className="evidence-card" key={item.id} onClick={() => setSelectedEvidence(item)}><div className="flex items-center justify-between gap-3"><span className="font-mono text-[10px] font-semibold text-teal-700">{item.id}</span><span className={`reliability-badge ${reliability.level}`}>可靠度 · {reliability.label}</span></div><p className="mt-5 text-left text-sm font-medium leading-6 text-slate-800">{item.content}</p><div className="mt-5 flex items-center justify-between border-t border-slate-100 pt-3 text-[11px] text-slate-400"><span>{item.period}</span><span className="flex items-center gap-1 text-slate-700">{item.sourcePage ? `P. ${item.sourcePage}` : "页码待核验"} · 查看原因 <ArrowIcon /></span></div></button>; })}</div></section> : null}

              {response.suggestedQuestions.length ? <section className="animate-rise pt-2"><p className="eyebrow">You may also want to ask</p><div className="mt-3 flex flex-wrap gap-2">{response.suggestedQuestions.map((item) => <button className="question-chip" key={item} onClick={() => void ask(item)}>{item}</button>)}</div></section> : null}
            </div> : null}
          </section>
        </div>
      </div>

      {selectedEvidence ? <EvidenceDrawer companyId={company.id} item={selectedEvidence} onClose={() => setSelectedEvidence(null)} /> : null}
    </main>
  );
}
