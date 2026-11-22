/**
 * 智能体执行流程图组件
 * 按 LangGraph 节点拓扑展示各步骤状态：最上方是会话图的“理解问题”，下面是单轮问数流水线
 */
import { Check, Circle, LoaderCircle, X } from "lucide-react";
import { cn } from "../lib/format";
import type { ProgressStatus, StepState } from "../types/agent";

type FlowStatus = ProgressStatus | "pending";

type FlowNode = {
  step: string;
  x: number;
  y: number;
  w?: number;
};

const nodes: FlowNode[] = [
  // 多轮会话入口：把追问改写成独立问题，后面是单轮问数流水线
  { step: "理解问题", x: 410, y: 20 },
  { step: "抽取关键词", x: 410, y: 112 },
  { step: "召回字段信息", x: 105, y: 204, w: 150 },
  { step: "召回指标信息", x: 315, y: 204, w: 150 },
  { step: "召回字段取值", x: 525, y: 204, w: 150 },
  { step: "召回业务知识", x: 735, y: 204, w: 150 },
  { step: "合并召回信息", x: 410, y: 306 },
  { step: "过滤指标信息", x: 290, y: 410 },
  { step: "过滤表信息", x: 530, y: 410 },
  { step: "添加额外上下文", x: 410, y: 514, w: 176 },
  { step: "生成SQL", x: 410, y: 618 },
  { step: "校验SQL", x: 410, y: 722 },
  { step: "校正SQL", x: 670, y: 722 },
  { step: "执行SQL", x: 410, y: 816 },
];

const connectors = [
  // 理解问题之后进入单轮流水线
  "M410 60 L410 106",
  // 关键词抽取后并行进入四路召回
  "M410 152 L410 176 L105 176 L105 198",
  "M410 152 L410 176 L315 176 L315 198",
  "M410 152 L410 176 L525 176 L525 198",
  "M410 152 L410 176 L735 176 L735 198",
  // 四路召回汇合到合并节点
  "M105 244 L105 270 L410 270 L410 300",
  "M315 244 L315 270 L410 270 L410 300",
  "M525 244 L525 270 L410 270 L410 300",
  "M735 244 L735 270 L410 270 L410 300",
  "M410 346 L410 374 L290 374 L290 404",
  "M410 346 L410 374 L530 374 L530 404",
  "M290 450 L290 478 L410 478 L410 508",
  "M530 450 L530 478 L410 478 L410 508",
  "M410 554 L410 612",
  "M410 658 L410 716",
  "M410 762 L410 810",
  "M488 742 L586 742",
  // 校正后的 SQL 回到校验节点重新检查
  "M670 762 L670 788 L470 788 L470 768",
];

const branchLabels = [
  { text: "有误", x: 530, y: 734 },
  { text: "重新校验", x: 540, y: 806 },
  { text: "无误", x: 366, y: 796 },
];

function getStatusMap(steps: StepState[]) {
  return steps.reduce<Record<string, StepState>>((map, item) => {
    map[item.step] = item;
    return map;
  }, {});
}

function NodeIcon({ status }: { status: FlowStatus }) {
  if (status === "running") {
    return <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />;
  }

  if (status === "success") {
    return <Check className="h-3.5 w-3.5" aria-hidden="true" />;
  }

  if (status === "error") {
    return <X className="h-3.5 w-3.5" aria-hidden="true" />;
  }

  return <Circle className="h-3.5 w-3.5" aria-hidden="true" />;
}

function FlowNodeCard({ node, state }: { node: FlowNode; state?: StepState }) {
  const width = node.w ?? 156;
  const status: FlowStatus = state?.status ?? "pending";
  const runs = state?.runs ?? 0;

  return (
    <div
      className="absolute -translate-x-1/2"
      style={{ left: node.x, top: node.y, width }}
      title={state?.detail}
    >
      <div
        className={cn(
          "flex h-10 items-center gap-2 border px-3 text-sm font-semibold shadow-line transition",
          status === "pending" && "border-ink/10 bg-white/55 text-ink/45",
          status === "running" && "border-brass/45 bg-brass/15 text-ink",
          status === "success" && "border-moss/25 bg-moss/10 text-ink",
          status === "error" && "border-tomato/35 bg-tomato/10 text-tomato",
        )}
      >
        <span
          className={cn(
            "grid h-6 w-6 shrink-0 place-items-center rounded-full",
            status === "pending" && "bg-ink/5 text-ink/35",
            status === "running" && "bg-brass/20 text-brass",
            status === "success" && "bg-moss/15 text-moss",
            status === "error" && "bg-tomato/15 text-tomato",
          )}
        >
          <NodeIcon status={status} />
        </span>
        <span className="min-w-0 flex-1 truncate">{node.step}</span>
        {runs > 1 && (
          <span className="shrink-0 text-xs font-medium text-ink/50" title={`执行了 ${runs} 次`}>
            ×{runs}
          </span>
        )}
      </div>
    </div>
  );
}

export function StepRail({ steps = [] }: { steps?: StepState[] }) {
  if (steps.length === 0) return null;

  const statusMap = getStatusMap(steps);

  return (
    <section className="mt-4 border border-ink/10 bg-white/40 px-3 py-4 shadow-line">
      <div className="mb-3 flex items-center justify-between gap-3 px-1">
        <div className="text-sm font-semibold text-ink">执行流程</div>
        <div className="text-xs text-ink/45">LangGraph</div>
      </div>

      <div className="overflow-x-auto">
        <div className="relative mx-auto h-[872px] w-[820px]">
          <svg
            className="pointer-events-none absolute inset-0 h-full w-full"
            viewBox="0 0 820 872"
            fill="none"
            aria-hidden="true"
          >
            <defs>
              <marker
                id="flow-arrow"
                markerHeight="8"
                markerWidth="8"
                orient="auto"
                refX="6"
                refY="4"
              >
                <path d="M0 0 L8 4 L0 8 Z" fill="rgba(32,32,29,0.58)" />
              </marker>
            </defs>
            {connectors.map((path) => (
              <path
                key={path}
                d={path}
                stroke="rgba(32,32,29,0.5)"
                strokeWidth="1.5"
                markerEnd="url(#flow-arrow)"
              />
            ))}
            {branchLabels.map((label) => (
              <text
                key={label.text}
                x={label.x}
                y={label.y}
                fill="rgba(32,32,29,0.62)"
                fontSize="13"
                fontWeight="600"
              >
                {label.text}
              </text>
            ))}
          </svg>

          {nodes.map((node) => (
            <FlowNodeCard key={node.step} node={node} state={statusMap[node.step]} />
          ))}
        </div>
      </div>
    </section>
  );
}
