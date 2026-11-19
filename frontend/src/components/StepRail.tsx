/**
 * 智能体执行流程图组件
 * 按 LangGraph 节点拓扑展示各步骤状态
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
  { step: "抽取关键词", x: 410, y: 20 },
  { step: "召回字段信息", x: 105, y: 112, w: 150 },
  { step: "召回指标信息", x: 315, y: 112, w: 150 },
  { step: "召回字段取值", x: 525, y: 112, w: 150 },
  { step: "召回业务知识", x: 735, y: 112, w: 150 },
  { step: "合并召回信息", x: 410, y: 214 },
  { step: "过滤指标信息", x: 290, y: 318 },
  { step: "过滤表信息", x: 530, y: 318 },
  { step: "添加额外上下文", x: 410, y: 422, w: 176 },
  { step: "生成SQL", x: 410, y: 526 },
  { step: "校验SQL", x: 410, y: 630 },
  { step: "校正SQL", x: 670, y: 630 },
  { step: "执行SQL", x: 410, y: 724 },
];

const connectors = [
  // 关键词抽取后并行进入四路召回
  "M410 60 L410 84 L105 84 L105 106",
  "M410 60 L410 84 L315 84 L315 106",
  "M410 60 L410 84 L525 84 L525 106",
  "M410 60 L410 84 L735 84 L735 106",
  // 四路召回汇合到合并节点
  "M105 152 L105 178 L410 178 L410 208",
  "M315 152 L315 178 L410 178 L410 208",
  "M525 152 L525 178 L410 178 L410 208",
  "M735 152 L735 178 L410 178 L410 208",
  "M410 254 L410 282 L290 282 L290 312",
  "M410 254 L410 282 L530 282 L530 312",
  "M290 358 L290 386 L410 386 L410 416",
  "M530 358 L530 386 L410 386 L410 416",
  "M410 462 L410 520",
  "M410 566 L410 624",
  "M410 670 L410 718",
  "M488 650 L586 650",
  // 校正后的 SQL 回到校验节点重新检查
  "M670 670 L670 696 L470 696 L470 676",
];

const branchLabels = [
  { text: "有误", x: 530, y: 642 },
  { text: "重新校验", x: 540, y: 714 },
  { text: "无误", x: 366, y: 704 },
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
        <div className="relative mx-auto h-[780px] w-[820px]">
          <svg
            className="pointer-events-none absolute inset-0 h-full w-full"
            viewBox="0 0 820 780"
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
