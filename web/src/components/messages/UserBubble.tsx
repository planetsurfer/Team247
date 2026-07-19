// User message bubble — right-aligned, #eeebe2, radius 18 18 4 18.
export function UserBubble({ text }: { text: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end" }}>
      <div
        style={{
          background: "#eeebe2",
          borderRadius: "18px 18px 4px 18px",
          padding: "10px 16px",
          maxWidth: "75%",
          fontSize: 14.5,
          lineHeight: 1.5,
        }}
      >
        {text}
      </div>
    </div>
  );
}
