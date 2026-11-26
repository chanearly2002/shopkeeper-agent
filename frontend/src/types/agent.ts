/**
 * 智能体类型定义
 * 定义问数智能体前端使用的 SSE 事件、流程步骤和聊天消息类型
 */
export type ProgressStatus = "running" | "success" | "error";

export type ProgressEvent = {
  type: "progress";
  step: string;
  status: ProgressStatus;
  /** 步骤附带的说明，例如 SQL 校验未通过时数据库返回的错误 */
  detail?: string;
};

export type ResultEvent = {
  type: "result";
  data: unknown;
  /** 最终执行的 SQL */
  sql?: string;
  /** 结果是否因超过行数上限被截断 */
  truncated?: boolean;
  max_rows?: number;
};

export type ErrorEvent = {
  type: "error";
  message: string;
};

/** 每次请求的第一条事件：本轮所属的会话 id，后续追问要带上它 */
export type SessionEvent = {
  type: "session";
  thread_id: string;
};

/** new：与上文无关的新问题；followup：结合上文的追问；result_qa：针对上一轮结果的提问；clarify：需要先反问 */
export type Intent = "new" | "followup" | "result_qa" | "clarify";

/** 追问改写结果：后端实际查询的独立问题 */
export type RewriteEvent = {
  type: "rewrite";
  standalone_question: string;
  intent: Intent;
};

/** 信息不足时的反问：回答时把选项或自己写的内容作为 resume 发回，会话从暂停处继续 */
export type ClarifyEvent = {
  type: "clarify";
  question: string;
  options: string[];
};

/** 针对上一轮结果的提问，直接基于结果给出的文字回答（不查库） */
export type AnswerEvent = {
  type: "answer";
  text: string;
};

export type AgentEvent =
  | ProgressEvent
  | ResultEvent
  | ErrorEvent
  | SessionEvent
  | RewriteEvent
  | ClarifyEvent
  | AnswerEvent;

export type StepState = {
  step: string;
  status: ProgressStatus;
  updatedAt: number;
  /** 该步骤进入 running 的次数，校验-修正循环中同一步骤会执行多次 */
  runs: number;
  detail?: string;
};

export type ResultMeta = {
  sql?: string;
  truncated?: boolean;
  maxRows?: number;
};

export type Rewrite = {
  standaloneQuestion: string;
  intent: Intent;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  status?: "streaming" | "done" | "error";
  /** 结合上文改写后的问题；只在与用户原话不同时展示 */
  rewrite?: Rewrite;
  /** 后端的反问；answered 表示已经回答或跳过，选项按钮不再可点 */
  clarify?: { question: string; options: string[]; answered?: boolean };
  steps?: StepState[];
  result?: unknown;
  resultMeta?: ResultMeta;
  error?: string;
};
