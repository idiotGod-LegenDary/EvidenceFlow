import type { FormEvent, KeyboardEvent, RefObject } from "react";
import { ArrowUp, Square } from "lucide-react";

interface ComposerProps {
  busy: boolean;
  onSend: (message: string) => void;
  onStop: () => void;
  draft: string;
  onDraft: (value: string) => void;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  canStop?: boolean;
}

export function Composer({ busy, onSend, onStop, draft, onDraft, inputRef, canStop = true }: ComposerProps) {
  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!busy && draft.trim()) { onSend(draft); onDraft(""); }
  }
  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }
  return <div className="composer-wrap">
    <form className="composer" onSubmit={submit}>
      <textarea ref={inputRef} value={draft} onChange={(event) => { onDraft(event.target.value); event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 180)}px`; }} onKeyDown={keyDown} placeholder="输入客户诉求与订单号，例如：取消订单 ORD10003" rows={1} maxLength={4000} aria-label="输入消息" />
      <div className="composer-bottom"><span className="composer-hint">Shift + Enter 换行</span>
        {busy ? <button type="button" className="send-button stop-button" disabled={!canStop} aria-label={canStop ? "停止生成" : "正在处理"} title={canStop ? "停止生成" : "正在执行，请等待结果"} onClick={onStop}><Square size={15} fill="currentColor" /></button>
          : <button type="button" className="send-button" aria-label="发送消息" title="发送消息" disabled={!draft.trim()} onClick={() => submit()}><ArrowUp size={19} strokeWidth={2.5} /></button>}
      </div>
    </form>
  </div>;
}
