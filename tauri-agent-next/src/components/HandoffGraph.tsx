import { GitBranch } from "lucide-react";
import { useMemo } from "react";
import {
  type HandoffEdgeView,
  type RunActorView,
  type WorkbenchTone,
  statusTone,
} from "../workbench";

type GraphNodeLayout = RunActorView & { layer: number; x: number; y: number };
type GraphEdgeLayout = HandoffEdgeView & { path: string; labelX: number; labelY: number };

function edgeTone(status: HandoffEdgeView["status"]): WorkbenchTone {
  return status === "active" ? "accent" : "success";
}

function buildGraphLayout(
  actors: readonly RunActorView[],
  edges: readonly HandoffEdgeView[],
): { nodes: GraphNodeLayout[]; edges: GraphEdgeLayout[]; height: number } {
  if (!actors.length) {
    return { nodes: [], edges: [], height: 360 };
  }
  const nodeWidth = 176;
  const nodeHeight = 58;
  const width = 920;
  const layerGap = 132;
  const top = 74;
  const padding = 88;

  const incoming = new Map<string, number>();
  const outgoing = new Map<string, HandoffEdgeView[]>();
  actors.forEach((actor) => incoming.set(actor.id, 0));
  edges.forEach((edge) => {
    incoming.set(edge.to, (incoming.get(edge.to) || 0) + 1);
    const list = outgoing.get(edge.from) || [];
    list.push(edge);
    outgoing.set(edge.from, list);
  });

  const roots = actors
    .filter((actor) => actor.isKnownSystem || (incoming.get(actor.id) || 0) === 0)
    .sort((left, right) => left.order - right.order)
    .map((actor) => actor.id);
  const rootIds = roots.length ? roots : [actors[0].id];

  const layers = new Map<string, number>();
  const queue = [...rootIds];
  rootIds.forEach((actorId) => layers.set(actorId, 0));

  while (queue.length) {
    const actorId = queue.shift();
    if (!actorId) {
      break;
    }
    const currentLayer = layers.get(actorId) || 0;
    (outgoing.get(actorId) || []).forEach((edge) => {
      const nextLayer = currentLayer + 1;
      const currentTargetLayer = layers.get(edge.to);
      if (currentTargetLayer === undefined || currentTargetLayer < nextLayer) {
        layers.set(edge.to, nextLayer);
        queue.push(edge.to);
      }
    });
  }

  let fallbackLayer = Math.max(0, ...Array.from(layers.values())) + 1;
  actors.forEach((actor) => {
    if (!layers.has(actor.id)) {
      layers.set(actor.id, edges.length ? fallbackLayer : 0);
      fallbackLayer += 1;
    }
  });

  const grouped = new Map<number, RunActorView[]>();
  actors.forEach((actor) => {
    const layer = layers.get(actor.id) || 0;
    const list = grouped.get(layer) || [];
    list.push(actor);
    grouped.set(layer, list);
  });

  const nodes: GraphNodeLayout[] = [];
  Array.from(grouped.entries())
    .sort(([leftLayer], [rightLayer]) => leftLayer - rightLayer)
    .forEach(([layer, layerActors]) => {
      const sorted = [...layerActors].sort((left, right) => {
        if (left.order !== right.order) {
          return left.order - right.order;
        }
        return left.name.localeCompare(right.name);
      });
      const usableWidth = width - padding * 2;
      const step = sorted.length > 1 ? usableWidth / (sorted.length - 1) : 0;
      sorted.forEach((actor, index) => {
        const x = sorted.length > 1 ? padding + step * index : width / 2;
        const y = top + layer * layerGap;
        nodes.push({ ...actor, layer, x, y });
      });
    });

  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const laidOutEdges = edges
    .map<GraphEdgeLayout | null>((edge) => {
      const source = nodeById.get(edge.from);
      const target = nodeById.get(edge.to);
      if (!source || !target) {
        return null;
      }
      if (target.layer > source.layer) {
        const startX = source.x;
        const startY = source.y + nodeHeight / 2 - 2;
        const endX = target.x;
        const endY = target.y - nodeHeight / 2 + 2;
        const midY = (startY + endY) / 2;
        return {
          ...edge,
          path: `M ${startX} ${startY} C ${startX} ${midY} ${endX} ${midY} ${endX} ${endY}`,
          labelX: (startX + endX) / 2,
          labelY: midY - 10,
        };
      }
      const offset = 136 + (source.layer - target.layer) * 28;
      const startX = source.x + nodeWidth / 2 - 10;
      const startY = source.y;
      const endX = target.x + nodeWidth / 2 - 10;
      const endY = target.y;
      return {
        ...edge,
        path: `M ${startX} ${startY} C ${startX + offset} ${startY} ${endX + offset} ${endY} ${endX} ${endY}`,
        labelX: Math.max(startX, endX) + offset - 42,
        labelY: (startY + endY) / 2 - 10,
      };
    })
    .filter((edge): edge is GraphEdgeLayout => Boolean(edge));

  const height = Math.max(360, top + Math.max(...nodes.map((node) => node.layer)) * layerGap + 120);
  return { nodes, edges: laidOutEdges, height };
}

export function HandoffGraph({
  actors,
  edges,
  selectedAgentId,
  selectedEdgeKey,
  onSelectActor,
  onSelectEdge,
}: {
  actors: readonly RunActorView[];
  edges: readonly HandoffEdgeView[];
  selectedAgentId: string | null;
  selectedEdgeKey: string | null;
  onSelectActor: (agentId: string) => void;
  onSelectEdge: (edge: HandoffEdgeView) => void;
}) {
  const layout = useMemo(() => buildGraphLayout(actors, edges), [actors, edges]);

  if (!layout.nodes.length) {
    return (
      <div className="graph-empty">
        <GitBranch size={18} />
        <p>暂无交接关系</p>
        <span>加载一个真实 run 后，这里会展示 Agent 之间已经发生的 handoff。</span>
      </div>
    );
  }

  return (
    <div className="graph-canvas">
      <svg viewBox={`0 0 920 ${layout.height}`} className="graph-svg">
        <defs>
          <marker id="graph-arr-accent" markerWidth="8" markerHeight="8" refX="5" refY="4" orient="auto">
            <polygon points="0 0, 8 4, 0 8" fill="#6ca8ff" />
          </marker>
          <marker id="graph-arr-success" markerWidth="8" markerHeight="8" refX="5" refY="4" orient="auto">
            <polygon points="0 0, 8 4, 0 8" fill="#5ad6ad" />
          </marker>
        </defs>
        {layout.edges.map((edge) => {
          const tone = edgeTone(edge.status);
          const selected = selectedEdgeKey === edge.key;
          return (
            <g key={edge.key} className={`graph-edge tone-${tone}${selected ? " is-selected" : ""}`}>
              <path
                d={edge.path}
                className="graph-edge-hit"
                onClick={() => onSelectEdge(edge)}
              />
              <path
                d={edge.path}
                className="graph-edge-line"
                markerEnd={`url(#graph-arr-${edge.status === "active" ? "accent" : "success"})`}
              />
              <g className="graph-edge-label" onClick={() => onSelectEdge(edge)}>
                <rect x={edge.labelX - 34} y={edge.labelY - 11} width="68" height="20" rx="10" />
                <text x={edge.labelX} y={edge.labelY + 4} textAnchor="middle">
                  {edge.topic}
                </text>
              </g>
              <text x={edge.labelX} y={edge.labelY + 20} textAnchor="middle" className="graph-edge-seq">
                seq {edge.firstSeq}
              </text>
            </g>
          );
        })}
        {layout.nodes.map((node) => {
          const selected = selectedAgentId === node.id;
          return (
            <g key={node.id} transform={`translate(${node.x - 88} ${node.y - 29})`}>
              <foreignObject width="176" height="58">
                <button
                  type="button"
                  className={`graph-node${selected ? " is-selected" : ""}`}
                  onClick={() => onSelectActor(node.id)}
                >
                  <span className={`graph-node-dot tone-${statusTone(node.status)}`} />
                  <span className="graph-node-copy">
                    <span className="graph-node-title">{node.name}</span>
                    <span className="graph-node-subtitle">{node.subtitle || node.status}</span>
                  </span>
                </button>
              </foreignObject>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
