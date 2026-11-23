import { CheckCircle2, ClipboardList, FileText, ShieldCheck } from "lucide-react";
import type { CaseDetail, CaseStatus } from "../types";

export const STATUS_LABELS: Record<CaseStatus, string> = {
  pending_review: "待审核", executing: "执行中", completed: "已完成",
  needs_review: "需重新审核", failed: "执行失败", cancelled: "已撤销",
};
const ORDER_LABELS: Record<string, string> = {
  created: "待支付", paid: "已支付，未发货", shipped: "已发货", delivered: "已签收",
  cancelled: "已取消", refunding: "退款处理中", refunded: "已退款",
};
const CHECK_LABELS: Record<string, string> = {
  order_matches: "目标订单匹配", order_cancelled: "订单已取消", no_extra_records: "未新增退款或退货记录",
  order_refunding: "订单进入退款流程", one_refund: "仅新增一笔退款申请", refund_matches: "退款金额与订单一致",
  one_return: "仅新增一笔退货申请", order_unchanged: "订单其他信息未改变", return_matches: "退货申请状态正确",
  order_fields_match: "订单其他字段保持一致",
};
function time(value: string) {
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function CasePanel({ detail, busy, onConfirm, onDismiss, onReview }: {
  detail: CaseDetail | null; busy: boolean; onConfirm: () => void; onDismiss: () => void; onReview: () => void;
}) {
  if (!detail) return <aside className="case-panel empty-evidence"><ClipboardList size={28} /><h2>处置依据</h2><p>提交明确的订单操作后，这里会保存订单事实、政策证据和处理记录。</p></aside>;
  const actionable = detail.status === "pending_review" || detail.status === "failed";
  const changed = [...detail.events].reverse().find((event) => event.kind === "review_required");
  const currentOrder = changed?.detail.order_current as Record<string, unknown> | undefined;
  return <aside className="case-panel" aria-label="工单依据与处理记录">
    <div className="case-panel-heading"><ShieldCheck size={18} /><h2>审核与执行</h2><span className={`case-badge ${detail.status}`}>{STATUS_LABELS[detail.status]}</span></div>
    <section className="case-section"><h3>订单事实</h3>
      <dl className="fact-list"><div><dt>订单编号</dt><dd>{detail.order_id}</dd></div>
        <div><dt>提议时状态</dt><dd>{ORDER_LABELS[String(detail.order_before.status)] ?? String(detail.order_before.status)}</dd></div>
        <div><dt>订单金额</dt><dd>¥{(Number(detail.order_before.total_cents) / 100).toFixed(2)}</dd></div>
        {currentOrder && <div><dt>复核时状态</dt><dd>{ORDER_LABELS[String(currentOrder.status)] ?? String(currentOrder.status)}</dd></div>}
      </dl>
    </section>
    <section className="case-section"><h3>待执行动作</h3><p className="action-summary">{detail.action.summary}</p>
      <p className={`case-notice ${detail.status}`} role="status">{detail.notice}</p>
      {detail.action.expires_at && <small className="muted">确认有效期至 {time(detail.action.expires_at)}</small>}
      <div className="case-buttons">
        {actionable && <button className="primary-action" disabled={busy} onClick={onConfirm}>{detail.status === "failed" ? "复核并重试" : "确认并执行"}</button>}
        {detail.status === "needs_review" && <button className="primary-action" disabled={busy} onClick={onReview}>重新生成处置建议</button>}
        {(actionable || detail.status === "needs_review") && <button disabled={busy} onClick={onDismiss}>撤销建议</button>}
      </div>
    </section>
    <section className="case-section"><h3><FileText size={14} /> 政策证据</h3>
      <p className="version-label" title={detail.knowledge_version}>知识快照 {detail.knowledge_version.slice(0, 12)}</p>
      {detail.evidence.map((item, index) => <details className="evidence-card" key={item.chunk_id} open={index === 0}>
        <summary>{item.title}</summary><p>{item.content}</p><small title={item.content_sha256}>正文校验 {item.content_sha256.slice(0, 12)}</small>
      </details>)}
      {!detail.evidence.length && <p className="muted">尚无可追溯证据，需要人工复核。</p>}
    </section>
    {Object.keys(detail.verification).length > 0 && <section className="case-section"><h3>结果验证</h3>
      {Object.entries(detail.verification).map(([key, passed]) => <div className="verification-row" key={key}><CheckCircle2 size={14} /><span>{CHECK_LABELS[key] ?? key}</span><b>{passed ? "通过" : "未通过"}</b></div>)}
      {Boolean(detail.result.refund_id) && <p className="muted">退款申请号：{String(detail.result.refund_id)}</p>}
      {Boolean(detail.result.return_id) && <p className="muted">退货申请号：{String(detail.result.return_id)}</p>}
    </section>}
    <section className="case-section"><h3>处理记录</h3><ol className="case-timeline">
      {detail.events.map((event, index) => <li key={`${event.kind}-${index}`}><span>{event.message}</span><time dateTime={event.created_at}>{time(event.created_at)}</time></li>)}
    </ol></section>
  </aside>;
}
