/**
 * 聊天消息气泡组件
 * 组合展示用户问题、智能体回复、追问改写、执行流程和结果表格
 */
import { Bot, Copy, PencilLine, UserRound } from "lucide-react";
import { ResultTable } from "./ResultTable";
import { StepRail } from "./StepRail";
import { cn, formatTime, toClipboardText } from "../lib/format";
import type { ChatMessage, Intent } from "../types/agent";

const intentLabels: Record<Intent, string> = {
  new: "新问题",
  followup: "追问",
  result_qa: "结果追问",
};

type MessageBubbleProps = {
  message: ChatMessage;
  /** 用户认为改写有误时调用，参数是改写后的问题 */
  onCorrectRewrite?: (standaloneQuestion: string) => void;
};

export function MessageBubble({ message, onCorrectRewrite }: MessageBubbleProps) {
  const isUser = message.role === "user";

  const copy = async () => {
    const text = message.result ? toClipboardText(message.result) : message.content;
    await navigator.clipboard.writeText(text);
  };

  return (
    <article className={cn("group flex gap-3", isUser && "justify-end")}>
      {!isUser && (
        <div className="mt-1 grid h-9 w-9 shrink-0 place-items-center rounded-full bg-ink text-parchment">
          <Bot className="h-4 w-4" aria-hidden="true" />
        </div>
      )}

      <div className={cn("max-w-[920px] flex-1", isUser && "flex max-w-[760px] justify-end")}>
        <div
          className={cn(
            "relative border px-5 py-4 shadow-line",
            isUser
              ? "border-ink/80 bg-ink text-parchment"
              : "border-ink/10 bg-[#fffaf1]/78 text-ink backdrop-blur",
          )}
        >
          {!isUser && message.rewrite && (
            <div className="mb-3 flex flex-wrap items-start gap-x-3 gap-y-2 border-l-2 border-brass/60 bg-brass/10 px-3 py-2 text-sm leading-6">
              <span className="shrink-0 border border-brass/35 px-1.5 text-xs font-semibold leading-5 text-brass">
                {intentLabels[message.rewrite.intent] ?? message.rewrite.intent}
              </span>
              <span className="min-w-0 flex-1 text-ink/80">
                我理解为：<span className="font-semibold text-ink">{message.rewrite.standaloneQuestion}</span>
              </span>
              {onCorrectRewrite && (
                <button
                  type="button"
                  onClick={() => onCorrectRewrite(message.rewrite!.standaloneQuestion)}
                  className="inline-flex shrink-0 items-center gap-1 text-xs font-semibold text-ink/55 transition hover:text-ink focus:outline-none focus:ring-2 focus:ring-moss/40"
                  title="把这句放回输入框，修改后重新提问"
                >
                  <PencilLine className="h-3.5 w-3.5" aria-hidden="true" />
                  理解有误
                </button>
              )}
            </div>
          )}

          <div className="flex items-start justify-between gap-3">
            <p className="whitespace-pre-wrap text-[15px] leading-7">{message.content}</p>
            {!isUser && message.status !== "streaming" && (
              <button
                type="button"
                onClick={copy}
                className="shrink-0 rounded-full p-1.5 text-ink/45 opacity-0 outline-none transition hover:bg-ink/5 hover:text-ink focus:opacity-100 focus:ring-2 focus:ring-moss/40 group-hover:opacity-100"
                title="复制"
                aria-label="复制"
              >
                <Copy className="h-4 w-4" aria-hidden="true" />
              </button>
            )}
          </div>

          {message.error && (
            <div className="mt-3 border border-tomato/30 bg-tomato/10 px-3 py-2 text-sm text-tomato">
              {message.error}
            </div>
          )}

          {!isUser && <StepRail steps={message.steps} />}
          {!isUser && message.result !== undefined && (
            <ResultTable data={message.result} meta={message.resultMeta} />
          )}

          <div
            className={cn(
              "mt-3 text-xs",
              isUser ? "text-parchment/55" : "text-ink/45",
            )}
          >
            {formatTime(message.createdAt)}
          </div>
        </div>
      </div>

      {isUser && (
        <div className="mt-1 grid h-9 w-9 shrink-0 place-items-center rounded-full bg-moss text-white">
          <UserRound className="h-4 w-4" aria-hidden="true" />
        </div>
      )}
    </article>
  );
}
