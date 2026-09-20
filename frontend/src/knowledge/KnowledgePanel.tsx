import { useEffect, useRef, useState } from "react";
import { FileText, Plus, Trash2, Upload } from "lucide-react";

import { createKnowledgeSpace, deleteKnowledgeDocument, getKnowledgeIngestionJob, listKnowledgeDocuments, listKnowledgeSpaces, uploadKnowledgeDocument } from "../api";
import { Modal } from "../components/ui/Modal";
import type { KnowledgeDocument, KnowledgeSpace } from "../types";

interface KnowledgePanelProps {
  open: boolean;
  token: string;
  selected: string[];
  onSelected: (slugs: string[]) => void;
  onClose: () => void;
}

const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

export function KnowledgePanel({ open, token, selected, onSelected, onClose }: KnowledgePanelProps) {
  const [spaces, setSpaces] = useState<KnowledgeSpace[]>([]);
  const [active, setActive] = useState("");
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  async function refreshSpaces(preferred?: string) {
    const values = await listKnowledgeSpaces(token);
    setSpaces(values);
    const next = preferred ?? active ?? values[0]?.slug ?? "";
    setActive(values.some((space) => space.slug === next) ? next : values[0]?.slug ?? "");
  }

  async function refreshDocuments(slug: string) {
    setDocuments(slug ? await listKnowledgeDocuments(token, slug) : []);
  }

  useEffect(() => {
    if (!open) return;
    setError(""); setStatus("");
    void refreshSpaces().catch((reason: Error) => setError(reason.message));
  }, [open, token]);

  useEffect(() => {
    if (!open) return;
    void refreshDocuments(active).catch((reason: Error) => setError(reason.message));
  }, [active, open, token]);

  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(""); setStatus("");
    try { await action(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "知识库操作失败。"); }
    finally { setBusy(false); }
  }

  async function createSpace() {
    const name = newName.trim();
    if (!name) return;
    const created = await createKnowledgeSpace(token, name);
    setNewName(""); setStatus("私有知识空间已创建。");
    await refreshSpaces(created.slug);
  }

  async function upload(file: File | undefined) {
    if (!file || !active) return;
    if (!/\.(md|txt|rst)$/i.test(file.name)) { setError("仅支持 UTF-8 的 MD、TXT、RST 文档。"); return; }
    if (file.size > 5 * 1024 * 1024) { setError("单个文档不能超过 5 MB。"); return; }
    const { job: queued } = await uploadKnowledgeDocument(token, active, file);
    setStatus(`已提交「${file.name}」，正在处理…`);
    let job = queued;
    for (let attempt = 0; attempt < 60 && ["pending", "processing"].includes(job.status); attempt += 1) {
      await wait(1000);
      job = await getKnowledgeIngestionJob(token, job.id);
    }
    if (job.status === "failed") throw new Error(job.error || "文档处理失败，请重试。");
    if (job.status !== "completed") throw new Error("文档仍在处理中，请稍后重新打开知识库查看。");
    setStatus(`「${file.name}」已可以检索。`);
    await Promise.all([refreshDocuments(active), refreshSpaces(active)]);
  }

  async function remove(document: KnowledgeDocument) {
    if (!active || !window.confirm(`确定删除「${document.title || document.source}」吗？`)) return;
    await deleteKnowledgeDocument(token, active, document.id);
    setStatus("文档已删除。下次检索不会再返回它。");
    await Promise.all([refreshDocuments(active), refreshSpaces(active)]);
  }

  function toggleSelection(slug: string) {
    onSelected(selected.includes(slug) ? selected.filter((item) => item !== slug) : [...selected, slug].slice(0, 10));
  }

  const current = spaces.find((space) => space.slug === active);
  const canEdit = current?.role === "owner" || current?.role === "editor";
  const canDelete = current?.role === "owner";

  return <Modal open={open} title="我的知识库" onClose={onClose}>
    <div className="knowledge-panel">
      <p className="muted small">勾选的空间会限定后续聊天检索范围；未勾选时搜索你有权访问的全部空间。</p>
      <div className="knowledge-create"><input value={newName} maxLength={100} onChange={(event) => setNewName(event.target.value)} placeholder="新私有空间名称" /><button className="secondary-button" disabled={busy || !newName.trim()} onClick={() => void perform(createSpace)}><Plus size={15} />创建</button></div>
      <div className="knowledge-layout">
        <div className="knowledge-spaces" aria-label="知识空间">{spaces.length === 0 ? <p className="muted">暂无知识空间</p> : spaces.map((space) => <div key={space.slug} className={`knowledge-space ${active === space.slug ? "active" : ""}`}><button type="button" onClick={() => setActive(space.slug)}><strong>{space.name}</strong><span>{space.document_count} 个文档 · {space.role === "owner" ? "所有者" : space.role === "editor" ? "可编辑" : "只读"}</span></button><label title="用于聊天检索"><input type="checkbox" checked={selected.includes(space.slug)} onChange={() => toggleSelection(space.slug)} />用于聊天</label></div>)}</div>
        <div className="knowledge-documents">
          <div className="knowledge-toolbar"><h3>{current?.name ?? "文档"}</h3>{canEdit && <><button className="secondary-button" disabled={busy} onClick={() => fileInput.current?.click()}><Upload size={15} />上传</button><input ref={fileInput} hidden type="file" accept=".md,.txt,.rst,text/plain,text/markdown" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; void perform(() => upload(file)); }} /></>}</div>
          {documents.length === 0 ? <p className="muted">当前空间暂无可用文档。</p> : documents.map((document) => <div className="knowledge-document" key={document.id}><FileText size={16} /><div><strong>{document.title || document.source}</strong><span>{new Date(document.updated_at).toLocaleString()}</span></div>{canDelete && <button className="icon-button" title="删除文档" aria-label={`删除 ${document.title || document.source}`} onClick={() => void perform(() => remove(document))}><Trash2 size={15} /></button>}</div>)}
        </div>
      </div>
      {busy && <p role="status">正在处理…</p>}{status && <p role="status">{status}</p>}{error && <p className="form-error" role="alert">{error}</p>}
    </div>
  </Modal>;
}
