import { ArrowRight } from "lucide-react";
import type { MessageExchangeView } from "../workbench";
import { formatTime } from "../workbench";

export function ExchangeCard({
  exchange,
  selected,
  onSelect,
  onFocusActor,
}: {
  exchange: MessageExchangeView;
  selected: boolean;
  onSelect: () => void;
  onFocusActor: (agentId: string) => void;
}) {
  return (
    <article className={`exchange-card${selected ? " is-selected" : ""}`} onClick={onSelect}>
      <div className="exchange-head">
        <div className="exchange-title">
          <span className="exchange-kicker">handoff</span>
          <strong>{exchange.topic}</strong>
        </div>
        <div className="exchange-meta">
          <span className={`exchange-badge tone-${exchange.receivedSeq ? "success" : "accent"}`}>
            {exchange.receivedSeq ? "received" : "in flight"}
          </span>
          <span className="exchange-clock">{formatTime(exchange.createdAt)}</span>
        </div>
      </div>
      <div className="exchange-route">
        <button
          type="button"
          className="inline-link"
          onClick={(event) => {
            event.stopPropagation();
            if (exchange.senderId) {
              onFocusActor(exchange.senderId);
            }
          }}
        >
          {exchange.senderName}
        </button>
        <ArrowRight size={12} />
        {exchange.receiverId ? (
          <button
            type="button"
            className="inline-link"
            onClick={(event) => {
              event.stopPropagation();
              onFocusActor(exchange.receiverId);
            }}
          >
            {exchange.receiverName}
          </button>
        ) : (
          <span>{exchange.receiverName}</span>
        )}
      </div>
      <p className="exchange-summary">{exchange.summary}</p>
      <div className="exchange-footer">
        <span>seq {exchange.seq}</span>
        <span>{exchange.objectType}</span>
        {exchange.messageId ? <span>{exchange.messageId.slice(0, 10)}</span> : null}
      </div>
    </article>
  );
}
