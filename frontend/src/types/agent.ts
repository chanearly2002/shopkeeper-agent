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

export type AgentEvent = ProgressEvent | ResultEvent | ErrorEvent;

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

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  status?: "streaming" | "done" | "error";
  steps?: StepState[];
  result?: unknown;
  resultMeta?: ResultMeta;
  error?: string;
};
