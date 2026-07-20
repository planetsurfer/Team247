// App shell — owns the theme, the admin-token bar, and switches between
// Landing (empty) and Thread (after first submit) on messages.length===0.
import { useState } from "react";
import { useChat } from "./useChat";
import { defaultTheme, type Theme } from "./theme";
import { Landing } from "./components/Landing";
import { Thread } from "./components/Thread";
import { AdminTokenBar } from "./components/AdminTokenBar";

export function App() {
  const [theme] = useState<Theme>(defaultTheme);
  const chat = useChat();
  const { state } = chat;
  const landing = state.messages.length === 0;

  // AdminTokenBar only surfaces on an explicit ?admin=1 (or once an admin
  // call is actually rejected) — it used to show by default to every visitor,
  // which doesn't fit a closed-beta audience that isn't admin.
  const adminRequested =
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).get("admin") === "1";
  const showTokenBar = (adminRequested && !state.adminToken) || !!state.tokenRejected;

  // beta-access gate (closed beta — PRODUCTION_ROADMAP.md P0 #1): hold the
  // landing view until the initial /api/auth/status probe resolves, so an
  // ungated task input never flashes before we know whether one is required.
  const betaGated = state.betaChecked && state.betaAuth && !state.betaAuthenticated;

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {showTokenBar && (
        <AdminTokenBar
          rejected={!!state.tokenRejected}
          onSave={chat.setAdminTokenState}
        />
      )}
      {!state.betaChecked ? (
        <div style={{ flex: 1 }} />
      ) : landing ? (
        <Landing
          theme={theme}
          input={state.input}
          onInput={chat.onInput}
          onKey={chat.onKey}
          onSend={chat.send}
          onPickRole={chat.pickRole}
          gate={betaGated}
          gateError={state.betaTokenError}
          onSubmitBetaToken={chat.submitBetaToken}
        />
      ) : (
        <Thread chat={chat} accent={theme.accent} />
      )}
    </div>
  );
}
