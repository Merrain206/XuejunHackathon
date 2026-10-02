"use client";

import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { ChartSpec } from "@/lib/types";

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

export function EvidenceChart({ chart }: { chart: ChartSpec }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const values = chart.series.flatMap((series) => series.values);
    const minValue = Math.floor(Math.min(0, ...values) / 10) * 10;
    const maxValue = Math.ceil(Math.max(0, ...values) / 10) * 10 || 10;
    const instance = echarts.init(containerRef.current, undefined, { renderer: "canvas" });
    instance.setOption({
      animationDuration: 900,
      animationEasing: "cubicOut",
      color: ["#123c3a", "#d39a2d"],
      tooltip: {
        trigger: "axis",
        valueFormatter: (value: unknown) => `${value}${chart.unit}`,
        backgroundColor: "#102b2b",
        borderWidth: 0,
        textStyle: { color: "#ffffff", fontSize: 12 },
      },
      legend: {
        top: 0,
        left: 0,
        itemWidth: 18,
        itemHeight: 2,
        textStyle: { color: "#526164", fontSize: 11 },
      },
      grid: { top: 54, right: 22, bottom: 28, left: 42 },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: chart.periods,
        axisLine: { lineStyle: { color: "#cdd7d3" } },
        axisTick: { show: false },
        axisLabel: { color: "#6b787a", fontSize: 11 },
      },
      yAxis: {
        type: "value",
        min: minValue,
        max: maxValue,
        axisLabel: { color: "#6b787a", fontSize: 10, formatter: `{value}${chart.unit}` },
        splitLine: { lineStyle: { color: "#e5ebe8", type: "dashed" } },
      },
      series: chart.series.map((series, index) => ({
        name: series.name,
        type: "line",
        data: series.values,
        smooth: 0.4,
        symbol: "circle",
        symbolSize: 8,
        lineStyle: { width: 3 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: index === 0 ? "rgba(18, 60, 58, 0.18)" : "rgba(211, 154, 45, 0.18)" },
            { offset: 1, color: "rgba(255, 255, 255, 0)" },
          ]),
        },
        emphasis: { focus: "series" },
        label: {
          show: true,
          position: "top",
          color: "#445255",
          fontSize: 10,
          formatter: `{c}${chart.unit}`,
        },
      })),
    });

    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      instance.dispose();
    };
  }, [chart]);

  return (
    <section className="chart-card animate-rise">
      <div className="flex flex-col justify-between gap-3 border-b border-slate-200 pb-4 sm:flex-row sm:items-end">
        <div>
          <p className="eyebrow">Evidence chart</p>
          <h3 className="mt-2 text-lg font-semibold tracking-tight text-slate-950">{chart.title}</h3>
          <p className="mt-1 text-xs text-slate-500">{chart.subtitle}</p>
        </div>
        <span className="font-mono text-[10px] text-teal-700">{chart.evidenceIds.join(" · ")}</span>
      </div>
      <div ref={containerRef} className="mt-5 h-[310px] w-full" role="img" aria-label={chart.title} />
    </section>
  );
}
