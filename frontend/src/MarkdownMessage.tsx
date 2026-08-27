import { Children, isValidElement, type ReactNode, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Check, Copy } from "lucide-react";

function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (!isValidElement<{ children?: ReactNode }>(node)) return "";
  return Children.toArray(node.props.children).map(nodeText).join("");
}

function CodeFrame({ children }: { children?: ReactNode }) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const child = Children.toArray(children)[0];
  const className = isValidElement<{ className?: string }>(child) ? child.props.className ?? "" : "";
  const language = className.match(/language-([\w-]+)/)?.[1] ?? "code";
  const code = nodeText(child).replace(/\n$/, "");

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(code);
      setCopyState("copied");
      window.setTimeout(() => setCopyState("idle"), 1600);
    } catch {
      setCopyState("failed");
    }
  }

  return <section className="code-frame">
    <header><span>{language}</span><button type="button" onClick={() => void copyCode()} aria-label="复制代码">{copyState === "copied" ? <Check size={14} /> : <Copy size={14} />}<span aria-live="polite">{copyState === "copied" ? "已复制" : copyState === "failed" ? "复制失败" : "复制"}</span></button></header>
    <pre>{children}</pre>
  </section>;
}

export function MarkdownMessage({ children }: { children: string }) {
  return <ReactMarkdown components={{ pre: ({ children: codeChildren }) => <CodeFrame>{codeChildren}</CodeFrame> }}>{children}</ReactMarkdown>;
}
