import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CasePanel } from "./CasePanel";
import type { CaseDetail } from "../types";

const detail: CaseDetail = {
  case_id: "case-1", session_id: "s-1", title: "取消订单", order_id: "ORD10003",
  status: "needs_review", notice: "订单信息已变化，请重新审核。", attempts: 1,
  created_at: "2026-11-21T00:00:00Z", updated_at: "2026-11-21T00:00:00Z",
  request_text: "取消订单 ORD10003", knowledge_version: "version-1",
  action: { action_id: "a-1", tool_name: "cancel_order", arguments: { order_id: "ORD10003" }, risk: "destructive", summary: "确认后取消订单。" },
  order_before: { status: "paid", total_cents: 7900 },
  evidence: [{ chunk_id: "p-1", title: "取消政策", content: "未发货可取消。", content_sha256: "hash", knowledge_version: "version-1" }],
  events: [{ kind: "review_required", message: "订单信息已变化", detail: { order_current: { status: "shipped" } }, created_at: "2026-11-21T00:00:00Z" }],
  messages: [], result: {}, verification: {},
};

describe("case review", () => {
  it("shows changed facts and requires a new proposal rather than confirmation", () => {
    const html = renderToStaticMarkup(<CasePanel detail={detail} busy={false} onConfirm={() => {}} onDismiss={() => {}} onReview={() => {}} />);
    expect(html).toContain("已支付，未发货"); expect(html).toContain("已发货");
    expect(html).toContain("未发货可取消"); expect(html).toContain("重新生成处置建议");
    expect(html).not.toContain("确认并执行");
  });
  it("shows verified completion without an execution button", () => {
    const html = renderToStaticMarkup(<CasePanel detail={{ ...detail, status: "completed", verification: { order_cancelled: true } }} busy={false} onConfirm={() => {}} onDismiss={() => {}} onReview={() => {}} />);
    expect(html).toContain("订单已取消"); expect(html).toContain("通过");
    expect(html).not.toContain("确认并执行"); expect(html).not.toContain("重新生成处置建议");
  });
});
