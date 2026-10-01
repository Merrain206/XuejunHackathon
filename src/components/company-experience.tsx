"use client";

import { FormEvent, useMemo, useState } from "react";
import { askCompany } from "@/lib/api";
import { company } from "@/lib/mock-data";
import type { AskResponse, Evidence } from "@/lib/types";
import { EvidenceChart } from "./evidence-chart";

const starterQuestions = [
  "你的收入结构发生了什么变化？",
  "哪个产品线增长最快？",
  "你目前有哪些值得关注的信号？",
];

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

function EvidenceDrawer({ item, onClose }: { item: Evidence; onClose: () => void }) {
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
            <dt className="text-slate-500">页码</dt><dd className="font-mono font-medium text-slate-900">P. {item.sourcePage}</dd>
          </dl>

          <div className="mt-7 rounded-sm border border-slate-200 bg-white p-5">
            <p className="eyebrow">Original excerpt</p>
            <blockquote className="mt-4 border-l-2 border-teal-600 pl-4 text-[15px] leading-7 text-slate-700">“{item.sourceQuote}”</blockquote>
          </div>

          <a className="mt-6 flex items-center justify-between border-b border-slate-950 pb-3 text-sm font-semibold text-slate-950 transition-colors hover:text-teal-700" href={`${item.sourceUrl}#page=${item.sourcePage}`} target="_blank" rel="noreferrer">
            打开上交所原始文件 <ArrowIcon direction="up" />
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

export function CompanyExperience() {
  const [question, setQuestion] = useState("");
  const [askedQuestion, setAskedQuestion] = useState("");
  const [response, setResponse] = useState<AskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);

  const metrics = useMemo(() => [
    { label: "2023 主营业务收入", value: "2.72 亿", note: "招股书口径" },
    { label: "便携式扫描仪占比", value: "57.87%", note: "2023" },
    { label: "跟踪式产品占比", value: "26.58%", note: "2023" },
  ], []);

  async function ask(nextQuestion?: string) {
    const prompt = (nextQuestion ?? question).trim();
    if (!prompt || loading) return;
    setQuestion(prompt);
    setAskedQuestion(prompt);
    setLoading(true);
    setError("");
    setResponse(null);
    try {
      setResponse(await askCompany(company.id, prompt));
    } catch {
      setError("这次检索没有完成，请稍后重试。");
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    void ask();
  }

  return (
    <main className="min-h-screen bg-[#f4f6f3] text-slate-950">
      <header className="border-b border-slate-200/90 bg-[#f8faf7]">
        <div className="mx-auto flex h-16 max-w-[1440px] items-center justify-between px-5 sm:px-8 lg:px-12">
          <div className="flex items-center gap-3"><ScanMark /><div><p className="text-sm font-semibold tracking-tight">ASK THE COMPANY</p><p className="text-[10px] uppercase tracking-[0.18em] text-slate-400">Evidence intelligence</p></div></div>
          <div className="flex items-center gap-2 text-xs text-slate-500"><span className="status-dot" />MOCK DATA</div>
        </div>
      </header>

      <div className="mx-auto grid max-w-[1440px] lg:grid-cols-[310px_minmax(0,1fr)]">
        <aside className="border-b border-slate-200 bg-[#eef1ed] px-5 py-7 sm:px-8 lg:min-h-[calc(100vh-64px)] lg:border-b-0 lg:border-r lg:px-8 lg:py-10">
          <p className="eyebrow">Company dossier</p>
          <div className="mt-5 flex items-start gap-4"><div className="company-monogram">S</div><div><h1 className="text-2xl font-semibold tracking-tight">{company.name}</h1><p className="mt-1 font-mono text-xs text-slate-500">SSE · {company.ticker}</p></div></div>
          <div className="mt-8 flex flex-wrap gap-2"><span className="tag">科创板</span><span className="tag">已上市</span></div>

          <dl className="mt-8 space-y-5 border-t border-slate-300 pt-6 text-sm">
            <div><dt className="text-xs text-slate-500">公司全称</dt><dd className="mt-1.5 leading-6 text-slate-800">{company.fullName}</dd></div>
            <div><dt className="text-xs text-slate-500">主营领域</dt><dd className="mt-1.5 leading-6 text-slate-800">{company.industry}</dd></div>
            <div><dt className="text-xs text-slate-500">上市日期</dt><dd className="mt-1.5 font-mono text-slate-800">{company.listedAt}</dd></div>
          </dl>

          <div className="mt-8 border-t border-slate-300 pt-6">
            <p className="eyebrow">Source coverage</p>
            <div className="mt-4 flex items-center justify-between text-sm"><span className="text-slate-600">公开文件</span><span className="font-mono font-semibold">01</span></div>
            <div className="mt-3 h-1 overflow-hidden bg-slate-300"><div className="h-full w-2/3 bg-teal-700" /></div>
            <p className="mt-3 text-xs leading-5 text-slate-500">当前演示仅使用已核验的招股书证据。</p>
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
                <button className="ask-button" type="submit" disabled={!question.trim() || loading}>{loading ? "检索中" : "提问"}<ArrowIcon /></button>
              </div>
            </form>

            {!response && !loading ? <div className="mt-4 flex flex-wrap gap-2">{starterQuestions.map((item) => <button className="question-chip" key={item} onClick={() => void ask(item)} type="button">{item}</button>)}</div> : null}
          </section>

          <section className="py-8">
            {!response && !loading && !error ? <div className="grid gap-3 sm:grid-cols-3">{metrics.map((metric) => <div className="metric-card" key={metric.label}><p className="text-xs text-slate-500">{metric.label}</p><p className="mt-4 text-2xl font-semibold tracking-tight">{metric.value}</p><p className="mt-1 font-mono text-[10px] uppercase tracking-wider text-slate-400">{metric.note}</p></div>)}</div> : null}

            {loading ? <div className="loading-panel" role="status"><div className="scanner-line" /><div className="flex items-center gap-3"><div className="loading-ring" /><div><p className="text-sm font-semibold">正在检索证据</p><p className="mt-1 text-xs text-slate-500">匹配原始文件并验证回答中的事实…</p></div></div></div> : null}
            {error ? <div className="border border-red-200 bg-red-50 p-5 text-sm text-red-800">{error}</div> : null}

            {response ? <div className="response-stack space-y-5">
              <div className="animate-rise"><p className="eyebrow">Question</p><p className="mt-2 text-sm font-medium text-slate-700">“{askedQuestion}”</p></div>
              <SignalCard response={response} onEvidence={setSelectedEvidence} />

              <article className="answer-card animate-rise">
                <div className="flex items-center justify-between border-b border-slate-200 pb-4"><p className="eyebrow">Evidence-based answer</p><span className="verified-label"><span>✓</span> 已验证</span></div>
                <p className="mt-6 max-w-3xl text-[17px] leading-8 text-slate-800">{response.answer}</p>
                {response.claims.length ? <div className="mt-7 border-t border-slate-200 pt-5"><p className="eyebrow">Key claims</p><div className="mt-3 space-y-3">{response.claims.map((claim, index) => <div className="flex gap-3 text-sm leading-6" key={claim.id}><span className="claim-index">{String(index + 1).padStart(2, "0")}</span><p className="flex-1 text-slate-700">{claim.text}</p><span className="font-mono text-[10px] text-teal-700">{claim.evidenceIds.join(" · ")}</span></div>)}</div></div> : null}
              </article>

              {response.charts.map((chart) => <EvidenceChart chart={chart} key={chart.id} />)}

              {response.evidence.length ? <section className="animate-rise"><div className="mb-3 flex items-center justify-between"><p className="eyebrow">Evidence trail</p><span className="font-mono text-[10px] text-slate-400">{response.evidence.length} ITEMS</span></div><div className="grid gap-3 xl:grid-cols-3">{response.evidence.map((item) => <button className="evidence-card" key={item.id} onClick={() => setSelectedEvidence(item)}><div className="flex items-center justify-between"><span className="font-mono text-[10px] font-semibold text-teal-700">{item.id}</span><span className="font-mono text-[10px] text-slate-400">P. {item.sourcePage}</span></div><p className="mt-5 text-left text-sm font-medium leading-6 text-slate-800">{item.content}</p><div className="mt-5 flex items-center justify-between border-t border-slate-100 pt-3 text-[11px] text-slate-400"><span>{item.period}</span><span className="flex items-center gap-1 text-slate-700">查看原文 <ArrowIcon /></span></div></button>)}</div></section> : null}

              {response.suggestedQuestions.length ? <section className="animate-rise pt-2"><p className="eyebrow">You may also want to ask</p><div className="mt-3 flex flex-wrap gap-2">{response.suggestedQuestions.map((item) => <button className="question-chip" key={item} onClick={() => void ask(item)}>{item}</button>)}</div></section> : null}
            </div> : null}
          </section>
        </div>
      </div>

      {selectedEvidence ? <EvidenceDrawer item={selectedEvidence} onClose={() => setSelectedEvidence(null)} /> : null}
    </main>
  );
}
