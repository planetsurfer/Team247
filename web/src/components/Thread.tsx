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
import { OpsQuestionsCard } from "./messages/OpsQuestionsCard";
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

  // Iteration 2 (try-your-agent chat) — the task text for the suggested
  // first-message chips: the first user turn in the thread (what the caller
  // originally described), falling back to the picked role name.
  const firstUserText = state.messages.find((m) => m.kind === "user")?.text;
  const chatKey = state.teamId && state.agentId ? `${state.teamId}:${state.agentId}` : undefined;

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
            if (m.kind === "team") {
              const opsState = state.teamId ? state.opsQuestionsByTeam[state.teamId] : undefined;
              return (
                <div key={i} style={{ display: "flex", flexDirection: "column", gap: 18 }}>
                  <TeamCard
                    roles={state.roles}
                    artifacts={state.artifacts}
                    accent={accent}
                    onToggle={chat.toggleRole}
                    onConfirm={chat.confirmTeam}
                    teamId={state.teamId}
                    userInputsState={
                      state.teamId ? state.userInputsByTeam[state.teamId] : undefined
                    }
                    onSaveInputs={(items) =>
                      state.teamId && chat.saveUserInputs(state.teamId, items)
                    }
                  />
                  {state.teamId && opsState && (
                    <OpsQuestionsCard
                      ops={opsState}
                      accent={accent}
                      enableTranscript={true}
                      transcript={state.transcriptByTeam[state.teamId]}
                      onAnswer={(idx, text) => chat.answerOpsQuestion(state.teamId!, idx, text)}
                      onSave={() => chat.saveOpsAnswers(state.teamId!)}
                      onDismiss={() => chat.dismissOpsCard(state.teamId!)}
                      onTranscriptToggle={() => chat.toggleTranscriptPanel(state.teamId!)}
                      onTranscriptTextChange={(text) =>
                        chat.setTranscriptText(state.teamId!, text)
                      }
                      onTranscriptExtract={() => chat.runTranscriptExtract(state.teamId!)}
                      onTranscriptItemEdit={(idx, text) =>
                        chat.editTranscriptItem(state.teamId!, idx, text)
                      }
                      onTranscriptItemRemove={(idx) =>
                        chat.removeTranscriptItem(state.teamId!, idx)
                      }
                      onTranscriptConfirm={() => chat.confirmTranscriptItems(state.teamId!)}
                      onTranscriptCancel={() => chat.cancelTranscript(state.teamId!)}
                    />
                  )}
                </div>
              );
            }
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
                  copyPromptBusy={!!state.copyPromptBusy}
                  copyPromptCopied={!!state.copyPromptCopied}
                  onCopyPrompt={chat.copyPrompt}
                  onAdjust={chat.adjust}
                  teamId={state.teamId}
                  feedback={state.teamId ? state.feedbackByTeam[state.teamId] : undefined}
                  onFeedbackThumb={(v) => state.teamId && chat.sendFeedback(state.teamId, v)}
                  onFeedbackCommentChange={(v) =>
                    state.teamId && chat.setFeedbackComment(state.teamId, v)
                  }
                  onFeedbackCommentSubmit={() =>
                    state.teamId && chat.submitFeedbackComment(state.teamId)
                  }
                  onFeedbackDismiss={() => state.teamId && chat.dismissFeedback(state.teamId)}
                  agentId={state.agentId}
                  useCase={firstUserText}
                  chat={chatKey ? state.chatByAgent[chatKey] : undefined}
                  onChatSend={(text) =>
                    state.teamId &&
                    state.agentId &&
                    chat.sendAgentChat(state.teamId, state.agentId, text)
                  }
                />
              );
            return null;
          })}
          {state.pending && <TypingDots />}
          {!state.pending && state.sendError && (
            <p style={{ margin: 0, fontSize: 12.5, color: C.gap }}>
              {state.sendError}
            </p>
          )}
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
