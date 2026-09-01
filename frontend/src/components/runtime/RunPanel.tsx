import { useEffect, useRef } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Brain, CheckCircle2, CircleAlert, Globe, ListTree, Loader2, Scale, Search, Sparkles, UserRoundCheck, Wrench } from "lucide-react";

import type { RunStep } from "../../types";

function stepIcon(step: RunStep) {
  const size = 15;
  if (step.kind === "memory") return <Brain size={size} />;
  if (step.kind === "tool") {
    switch (step.title) {
      case "知识库检索": return <Search size={size} />;
      case "网页搜索": return <Globe size={size} />;
      case "人工确认": return <UserRoundCheck size={size} />;
      default: return <Wrench size={size} />;
    }
  }
  if (step.kind === "rag") {
    switch (step.title) {
      case "查询规划": return <ListTree size={size} />;
      case "证据评估": return <Scale size={size} />;
      default: return <ListTree size={size} />;
    }
  }
  if (step.kind === "llm") return <Sparkles size={size} />;
  if (step.kind === "error") return <CircleAlert size={size} />;
  return <CheckCircle2 size={size} />;
}

interface RunPanelProps {
  steps: RunStep[];
  streaming: boolean;
}

const SPRING = { type: "spring", stiffness: 460, damping: 30 } as const;

export function RunPanel({ steps, streaming }: RunPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const reduceMotion = useReducedMotion();
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [steps]);

  const pop = reduceMotion ? { duration: 0.1 } : SPRING;

  return <aside className="run-panel">
    <header className="run-header">
      <div><p className="run-eyebrow">LIVE EXECUTION</p><motion.h2 initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={pop}>运行过程</motion.h2></div>
      <div className="run-header-actions">
        <motion.span key={streaming ? "live" : "idle"} className={`run-live ${streaming ? "active" : ""}`} initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} transition={pop}>{streaming ? <Loader2 size={12} className="spin" /> : <CheckCircle2 size={12} />}{streaming ? "运行中" : "已结束"}</motion.span>
      </div>
    </header>
    <div className="run-body" ref={scrollRef}>
      {steps.length === 0
        ? <p className="run-empty">发送一条消息后，这里会实时显示 Agent 的执行过程——调用了哪些工具、查了什么、记忆状态等。</p>
        : <ol className="run-timeline">
            <AnimatePresence initial={false}>
              {steps.map((step, index) => (
                <motion.li
                  key={step.id}
                  layout={!reduceMotion}
                  className={`run-step ${step.kind} ${step.status}`}
                  initial={reduceMotion ? { opacity: 0 } : { opacity: 0, x: 28, scale: 0.96 }}
                  animate={reduceMotion ? { opacity: 1 } : { opacity: 1, x: 0, scale: 1 }}
                  exit={reduceMotion ? { opacity: 0 } : { opacity: 0, x: -18, scale: 0.96 }}
                  transition={reduceMotion ? { duration: 0.12 } : { type: "spring", stiffness: 460, damping: 30, delay: Math.min(index * 0.06, 0.5) }}
                >
                  <span className="run-step-icon-wrap">
                    <motion.span key={step.status} className="run-step-icon" initial={{ scale: reduceMotion ? 1 : 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={pop}>{stepIcon(step)}</motion.span>
                    {step.status === "running" && <span className="run-ping" />}
                  </span>
                  <div className="run-step-body">
                    <div className="run-step-title">{step.title}<span className="run-step-status">{step.status === "running" ? "进行中" : step.status === "done" ? "完成" : "失败"}</span></div>
                    <motion.p className="run-step-detail" key={step.detail ?? ""} initial={{ opacity: 0, y: reduceMotion ? 0 : 2 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduceMotion ? 0.1 : 0.2 }}>{step.detail}</motion.p>
                  </div>
                </motion.li>
              ))}
            </AnimatePresence>
          </ol>}
    </div>
  </aside>;
}
