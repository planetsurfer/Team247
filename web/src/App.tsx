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

  const showTokenBar = !state.adminToken || !!state.tokenRejected;

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
      {landing ? (
        <Landing
          theme={theme}
          input={state.input}
          onInput={chat.onInput}
          onKey={chat.onKey}
          onSend={chat.send}
          onPickRole={chat.pickRole}
        />
      ) : (
        <Thread chat={chat} accent={theme.accent} />
      )}
    </div>
  );
}
