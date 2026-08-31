import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Activity, Check, CircleAlert, Database, Gauge, LoaderCircle, Zap } from "lucide-react";

import type { CacheStats, ReasoningEffort, StageItem, TokenUsage } from "../../types";

interface RuntimePanelProps {
  stages: StageItem[];
  usage: TokenUsage;
  cache: CacheStats;
  estimatedOutput: number;
  streaming: boolean;
  effort: ReasoningEffort;
}

const STAGE_LABELS: Record<string, string> = {
  memory: "Memory retrieval",
  agent: "Agent execution",
  tool: "Tool call",
  generation: "LLM generation",
};

function StageIcon({ status }: { status: StageItem["status"] }) {
  if (status === "completed") return <Check size={12} />;
  if (status === "failed") return <CircleAlert size={12} />;
  return <LoaderCircle size={12} />;
}

export function RuntimePanel({ stages, usage, cache, estimatedOutput, streaming, effort }: RuntimePanelProps) {
  const reduceMotion = useReducedMotion();
  return <div className="runtime-panel">
    <header className="runtime-header"><div><p className="eyebrow">LIVE RUNTIME</p><h2>Execution</h2></div><span className={`runtime-live ${streaming ? "active" : ""}`}><Activity size={14} />{streaming ? "Running" : "Idle"}</span></header>
    <section className="runtime-section"><h3>Agent timeline</h3><div className="timeline"><AnimatePresence initial={false}>{stages.length === 0 ? <p className="runtime-empty">发送问题后显示真实执行阶段。</p> : stages.map((stage) => <motion.div layout className={`timeline-step ${stage.status}`} key={stage.stage} initial={{ opacity: 0, y: reduceMotion ? 0 : 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}><span className="timeline-marker"><StageIcon status={stage.status} /></span><div><strong>{STAGE_LABELS[stage.stage] ?? stage.stage}</strong><small>{stage.elapsedMs ? `${stage.elapsedMs} ms` : stage.status}</small></div></motion.div>)}</AnimatePresence></div></section>
    <section className="runtime-section"><h3>Usage</h3><dl className="runtime-metrics"><div><dt><Zap size={14} />Tokens</dt><dd>{usage.exact ? usage.total : `~${estimatedOutput}`}</dd><small>{usage.exact ? `input ${usage.input} · output ${usage.output}` : "stream estimate"}</small></div><div><dt><Gauge size={14} />Reasoning</dt><dd>{effort}</dd><small>{usage.exact ? `${usage.reasoning} reasoning tokens` : "request setting"}</small></div><div><dt><Database size={14} />Memory cache</dt><dd>{Math.round(cache.hitRate * 100)}%</dd><small>{cache.hits} hits · {cache.misses} misses</small></div></dl></section>
    <footer className="runtime-footnote"><span />Metrics reflect the current backend instance.</footer>
  </div>;
}
