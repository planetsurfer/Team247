// Three blinking dots shown between submit and the response card.
// Prototype: sc-if pending → three 7px #b3ad9f dots, blink 1.1s staggered.
export function TypingDots() {
  const dot = (delay: number) => ({
    display: "inline-block",
    width: 7,
    height: 7,
    borderRadius: "50%",
    background: "#b3ad9f",
    animation: `blink 1.1s ${delay}s infinite`,
  });
  return (
    <div style={{ display: "flex", gap: 5, padding: "12px 4px" }}>
      <span style={dot(0)} />
      <span style={dot(0.18)} />
      <span style={dot(0.36)} />
    </div>
  );
}
