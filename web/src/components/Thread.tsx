// Thread state — header + scrolling message list (auto-scroll) + docked composer.
// Dispatches each message to its card; card data (roles/skills/prove) lives in
// the shared chat state, exactly like the prototype where messages are kind tags.
import { useEffect, useRef } from "react";
import type { useChat } from "../useChat";
import { C } from "../theme";
import { Header } from "./Header";
import { Composer } from "./Composer";
import { TypingDots } from "./TypingDots";
import { UserBubble } from "./messages/UserBubble";
import { TeamCard } from "./messages/TeamCard";
import { LoadoutCard } from "./messages/LoadoutCard";
import { ProveCard } from "./messages/ProveCard";
import { DeliverCard } from "./messages/DeliverCard";

interface ThreadProps {
  chat: ReturnType<typeof useChat>;
  accent: string;
}

export function Thread({ chat, accent }: ThreadProps) {
  const { state } = chat;
  const listRef = useRef<HTMLDivElement>(null);

  // auto-scroll to bottom on new content (ports componentDidUpdate)
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [state.messages.length, state.pending, state.prove?.t]);

  return (
    <>
      <Header accent={accent} />
      <div ref={listRef} style={{ flex: 1, overflowY: "auto" }}>
        <div
          style={{
            maxWidth: 780,
            margin: "0 auto",
            padding: "26px 20px 24px",
            display: "flex",
            flexDirection: "column",
            gap: 18,
          }}
        >
          {state.messages.map((m, i) => {
            if (m.kind === "user") return <UserBubble key={i} text={m.text} />;
            if (m.kind === "team")
              return (
                <TeamCard
                  key={i}
                  roles={state.roles}
                  artifacts={state.artifacts}
                  accent={accent}
                  onToggle={chat.toggleRole}
                  onConfirm={chat.confirmTeam}
                />
              );
            if (m.kind === "loadout")
              return (
                <LoadoutCard
                  key={i}
                  roleName={state.roleName}
                  skills={state.skills}
                  running={state.running}
                  accent={accent}
                  onBaseline={chat.onBaseline}
                  onSpecialize={chat.onSpecialize}
                  onMax={chat.onMax}
                  onDec={chat.dec}
                  onInc={chat.inc}
                  onGenerate={chat.generate}
                />
              );
            if (m.kind === "prove")
              return (
                <ProveCard
                  key={i}
                  prove={state.prove}
                  skills={state.skills}
                  accent={accent}
                  onRetry={chat.generate}
                />
              );
            if (m.kind === "deliver")
              return (
                <DeliverCard
                  key={i}
                  roleName={state.roleName}
                  skills={state.skills}
                  copied={state.copied}
                  accent={accent}
                  bundleBusy={!!state.bundleBusy}
                  onDownload={chat.download}
                  onDownloadBundle={chat.downloadBundle}
                  onCopy={chat.copy}
                  onAdjust={chat.adjust}
                />
              );
            return null;
          })}
          {state.pending && <TypingDots />}
        </div>
      </div>
      <Composer
        input={state.input}
        accent={accent}
        onInput={chat.onInput}
        onKey={chat.onKey}
        onSend={chat.send}
      />
    </>
  );
}
