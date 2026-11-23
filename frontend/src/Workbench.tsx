import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Inbox, Plus, RefreshCw } from "lucide-react";
import { Composer } from "./components/Composer";
import { MessageView } from "./components/MessageView";
import { CasePanel, STATUS_LABELS } from "./components/CasePanel";
import { checkHealth, streamChat } from "./lib/api";
import { caseCommand, getCase, listCases, WORKSPACE_USER } from "./lib/cases";
import type { CaseDetail, CaseStatus, CaseSummary, ChatResponse, Conversation, Message } from "./types";
import "./workbench.css";

const SUGGESTIONS = [
  { title: "取消未发货订单", text: "取消订单 ORD10003", note: "保存处置依据，确认后执行" },
  { title: "申请退货", text: "我要退货 ORD10001，原因是商品不合适", note: "核对订单，创建退货申请" },
  { title: "提交退款申请", text: "我要退款 ORD10003，原因是不想要了", note: "申请提交后核验业务记录" },
  { title: "核对政策", text: "定制商品有质量问题可以退货吗？", note: "查阅资料，保留来源引用" },
];

function fromDetail(detail: CaseDetail): Conversation {
  return {
    id: detail.case_id, title: detail.title, sessionId: detail.session_id, updatedAt: Date.now(),
    messages: detail.messages.filter((m) => m.role === "user" || m.role === "assistant")
      .map((m, index) => ({ id: `${detail.case_id}-${index}`, role: m.role as "user" | "assistant", content: m.content, state: "complete" })),
  };
}

export default function Workbench() {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [filter, setFilter] = useState<CaseStatus | "all">("all");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const controller = useRef<AbortController | null>(null);
  const selection = useRef(0);
  const busyRef = useRef(false);

  useEffect(() => {
    let active = true;
    void listCases().then((items) => { if (active) setCases(items); }).catch(() => { if (active) setError("无法加载工单，请检查服务连接。"); });
    void checkHealth().then((value) => { if (active) setHealthy(value); });
    return () => { active = false; selection.current++; controller.current?.abort(); };
  }, []);

  async function refresh(id?: string, restoreMessages = false) {
    const token = ++selection.current;
    const [items, current] = await Promise.all([listCases(), id ? getCase(id) : Promise.resolve(null)]);
    if (token !== selection.current) return;
    setCases(items); setDetail(current);
    if (current && restoreMessages) setConversation(fromDetail(current));
  }

  async function openCase(id: string) {
    if (busyRef.current) return;
    setError("");
    try { await refresh(id, true); } catch (cause) { setError(cause instanceof Error ? cause.message : "工单加载失败。"); }
  }

  function newCase() {
    if (busyRef.current) return;
    selection.current++;
    setDetail(null); setConversation(null); setDraft(""); setError("");
    inputRef.current?.focus();
  }

  async function send(raw: string) {
    const text = raw.trim();
    if (!text || busyRef.current) return;
    busyRef.current = true; setBusy(true); setError("");
    const current: Conversation = conversation ?? { id: crypto.randomUUID(), title: text.slice(0, 32), updatedAt: Date.now(), messages: [] };
    const user: Message = { id: crypto.randomUUID(), role: "user", content: text, state: "complete" };
    const assistant: Message = { id: crypto.randomUUID(), role: "assistant", content: "", state: "streaming" };
    setConversation({ ...current, messages: [...current.messages, user, assistant] });
    const abort = new AbortController(); controller.current = abort;
    let reply: ChatResponse | null = null;
    try {
      await streamChat({ message: text, user_id: WORKSPACE_USER, ...(current.sessionId ? { session_id: current.sessionId } : {}) }, (event) => {
        if (event.event === "token") setConversation((old) => old ? { ...old, messages: old.messages.map((m) => m.id === assistant.id ? { ...m, content: m.content + event.text } : m) } : old);
        if (event.event === "answer") {
          reply = event.data;
          setConversation((old) => old ? { ...old, sessionId: event.data.session_id, messages: old.messages.map((m) => m.id === assistant.id ? { ...m, content: event.data.answer, state: "complete", citations: event.data.citations } : m) } : old);
        }
      }, abort.signal);
      if (!reply) throw new Error("连接结束，未取得处理结果。");
      const result = reply as ChatResponse;
      await refresh(result.case_id ?? detail?.case_id, false);
    } catch (cause) {
      const message = abort.signal.aborted ? "已停止当前请求；已保存的工单可刷新查看。" : cause instanceof Error ? cause.message : "请求失败。";
      setError(message);
      setConversation((old) => old ? { ...old, messages: old.messages.map((m) => m.id === assistant.id && m.state === "streaming" ? { ...m, content: m.content || message, state: "stopped" } : m) } : old);
    } finally { busyRef.current = false; setBusy(false); controller.current = null; }
  }

  async function act(command: "confirm" | "dismiss") {
    if (!detail || busyRef.current) return;
    busyRef.current = true; setBusy(true); setError("");
    try { await caseCommand(detail.case_id, command); await refresh(detail.case_id, true); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败，请刷新工单确认状态。"); }
    finally { busyRef.current = false; setBusy(false); }
  }

  const shown = cases.filter((item) => filter === "all" || item.status === filter);
  const pending = cases.filter((item) => item.status === "pending_review").length;
  return <div className="desk-shell">
    <aside className="case-sidebar">
      <div className="desk-brand"><span className="desk-symbol">E</span><div><strong>EvidenceFlow</strong><small>售后工单工作台</small></div></div>
      <button className="new-case" disabled={busy} onClick={newCase}><Plus size={17} /> 新建处理</button>
      <div className="case-list-heading"><span>工单记录</span><button title="刷新工单" aria-label="刷新工单" disabled={busy} onClick={() => void refresh(detail?.case_id, !!detail).catch(() => setError("刷新失败，请重试。"))}><RefreshCw size={14} /></button></div>
      <label className="filter-label">筛选状态<select aria-label="筛选工单状态" value={filter} onChange={(event) => setFilter(event.target.value as CaseStatus | "all")}><option value="all">全部状态</option>{Object.entries(STATUS_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <nav className="desk-case-list" aria-label="工单列表">{shown.map((item) => <button disabled={busy} className={`desk-case ${detail?.case_id === item.case_id ? "active" : ""}`} key={item.case_id} onClick={() => void openCase(item.case_id)}>
        <div><b>{item.order_id}</b><span className={`case-badge ${item.status}`}>{STATUS_LABELS[item.status]}</span></div><strong>{item.title}</strong><small>{item.notice}</small>
      </button>)}{!shown.length && <div className="case-list-empty"><Inbox size={22} /><p>{cases.length ? "当前状态下没有工单" : "提交订单操作后生成工单"}</p></div>}</nav>
      <div className="desk-footer"><span>最近 {cases.length} 条 · {pending} 条待审核</span><small>本地演示环境</small></div>
    </aside>
    <main className="desk-main">
      <header className="desk-header"><div><span className="eyebrow">售后处理</span><h1>{detail ? detail.order_id : "新建处理"}</h1></div><span className={`desk-connection ${healthy === false ? "offline" : ""}`}>{healthy === null ? "连接中" : healthy ? "服务已连接" : "服务未连接"}</span></header>
      {error && <div className="desk-error" role="alert">{error}</div>}
      {conversation ? <div className="chat-content"><MessageView conversation={conversation} busy={busy} onConfirm={() => void act("confirm")} onCancel={() => void act("dismiss")} /></div> : <div className="desk-welcome"><span className="eyebrow">订单与政策核对</span><h2>核对事实，<br />处理售后。</h2><p>审核具体操作及其依据，执行后验证业务结果。</p><div className="desk-suggestions">{SUGGESTIONS.map((item) => <button key={item.title} onClick={() => { setDraft(item.text); inputRef.current?.focus(); }}><span><strong>{item.title}</strong><small>{item.note}</small></span><ArrowUpRight size={17} /></button>)}</div></div>}
      <Composer busy={busy} canStop={controller.current !== null} onSend={(text) => void send(text)} onStop={() => controller.current?.abort()} draft={draft} onDraft={setDraft} inputRef={inputRef} />
    </main>
    <CasePanel detail={detail} busy={busy} onConfirm={() => void act("confirm")} onDismiss={() => void act("dismiss")} onReview={() => detail && void send(detail.request_text)} />
  </div>;
}
