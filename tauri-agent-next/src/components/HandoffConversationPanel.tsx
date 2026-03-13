import { ArrowRight } from "lucide-react";
import type { MessageExchangeView, RunInfoView } from "../workbench";
import { formatTime, statusTone } from "../workbench";

export function HandoffConversationPanel({
  runInfo,
  exchanges,
  selectedExchange,
  selectedExchangeId,
  autoFollow,
  onToggleAutoFollow,
  onSelectExchange,
  onFocusActor,
}: {
  runInfo: RunInfoView;
  exchanges: readonly MessageExchangeView[];
  selectedExchange: MessageExchangeView | null;
  selectedExchangeId: string | null;
  autoFollow: boolean;
  onToggleAutoFollow: () => void;
  onSelectExchange: (exchangeId: string) => void;
  onFocusActor: (agentId: string) => void;
}) {
  const highlightedExchange = selectedExchange || exchanges[exchanges.length - 1] || null;
  const outcomeText = runInfo.error || runInfo.reply;

  return (
    <>
      <div className="side-header">
        <div>
          <span className="side-title-label">对话记录</span>
          <strong className="side-title">Multi-Agent Stream</strong>
        </div>
        <div className="side-header-actions">
          <span className={`detail-chip tone-${statusTone(runInfo.status)}`}>{runInfo.status}</span>
          <button type="button" className={`ghost-button${autoFollow ? " is-toggled" : ""}`} onClick={onToggleAutoFollow}>
            {autoFollow ? "Auto Follow" : "Manual"}
          </button>
        </div>
      </div>

      <div className="summary-stack">
        <section className="summary-card">
          <div className="section-head">
            <h3>Run Summary</h3>
            <span>{runInfo.strategy || "no strategy"}</span>
          </div>
          <div className="summary-meta-row">
            <span className={`detail-chip tone-${statusTone(runInfo.status)}`}>{runInfo.status}</span>
            <span className="stream-time">seq {runInfo.latestSeq}</span>
          </div>
          <p className="summary-copy">{outcomeText || "当前 run 还没有 reply / error 摘要。"}</p>
        </section>

        <section className="summary-card">
          <div className="section-head">
            <h3>Selected Handoff</h3>
            <span>{highlightedExchange ? `seq ${highlightedExchange.seq}` : "none"}</span>
          </div>
          {highlightedExchange ? (
            <>
              <div className="summary-route">
                <button type="button" className="inline-link" onClick={() => onFocusActor(highlightedExchange.senderId)}>
                  {highlightedExchange.senderName}
                </button>
                <ArrowRight size={10} />
                {highlightedExchange.receiverId ? (
                  <button type="button" className="inline-link" onClick={() => onFocusActor(highlightedExchange.receiverId)}>
                    {highlightedExchange.receiverName}
                  </button>
                ) : (
                  <span>{highlightedExchange.receiverName}</span>
                )}
              </div>
              <p className="summary-copy">{highlightedExchange.summary}</p>
              <div className="summary-meta-row">
                <span className="exchange-badge">{highlightedExchange.topic}</span>
                <span className="stream-time">{formatTime(highlightedExchange.createdAt)}</span>
              </div>
            </>
          ) : (
            <p className="summary-copy">选中一条真实 handoff 后，这里会展示 sender、target、topic 和摘要。</p>
          )}
        </section>
      </div>

      <div className="stream-list">
        {exchanges.length ? (
          exchanges.map((exchange, index) => {
            const previous = exchanges[index - 1];
            const showSeparator = !previous || previous.topic !== exchange.topic;
            return (
              <div key={exchange.id}>
                {showSeparator ? (
                  <div className="round-divider">
                    <div className="round-divider-line" />
                    <span>{exchange.topic}</span>
                    <div className="round-divider-line" />
                  </div>
                ) : null}
                <div className="stream-item">
                  <div className="stream-item-head">
                    <div className="stream-route">
                      <button type="button" className="inline-link" onClick={() => onFocusActor(exchange.senderId)}>
                        {exchange.senderName}
                      </button>
                      <ArrowRight size={10} />
                      {exchange.receiverId ? (
                        <button type="button" className="inline-link" onClick={() => onFocusActor(exchange.receiverId)}>
                          {exchange.receiverName}
                        </button>
                      ) : (
                        <span>{exchange.receiverName}</span>
                      )}
                    </div>
                    <span className="stream-time">{formatTime(exchange.createdAt)}</span>
                  </div>
                  <button
                    type="button"
                    className={`stream-bubble${selectedExchangeId === exchange.id ? " is-selected" : ""}`}
                    onClick={() => onSelectExchange(exchange.id)}
                  >
                    <div className="stream-bubble-head">
                      <span>{exchange.topic}</span>
                      <span>seq {exchange.seq}</span>
                    </div>
                    <p>{exchange.summary}</p>
                  </button>
                </div>
              </div>
            );
          })
        ) : (
          <div className="graph-empty">
            <p>No handoffs yet.</p>
            <span>创建或加载一个 run 后，这里会展示真实的 Agent 交接流。</span>
          </div>
        )}
      </div>

      <div className="conversation-footer">
        <div className="conversation-footer-main">
          <span className="conversation-footer-label">run</span>
          <strong>{runInfo.runId || "none"}</strong>
        </div>
        <div className="conversation-footer-meta">
          <span>{exchanges.length} exchanges</span>
          <span>{runInfo.latestSeq} seq</span>
        </div>
      </div>
    </>
  );
}
