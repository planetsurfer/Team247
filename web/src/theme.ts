// Design tokens for the Team247 chat frontend.
// Sourced verbatim from the handoff README §Design Tokens + the prototype <helmet>.
// Per-component inline styles read from `tokens` so every card shares one truth.

export type Tone = "friendly" | "technical";
export type Suggestions = "both" | "tasks" | "roles" | "none";

export interface Theme {
  accent: string;
  tone: Tone;
  suggestions: Suggestions;
}

export const defaultTheme: Theme = {
  accent: "#0e7a70",
  tone: "friendly",
  suggestions: "both",
};

// Color palette (handoff §Colors)
export const C = {
  pageBg: "#faf9f7",
  ink: "#1c1c1a",
  muted: "#7d7869", // muted text
  dim: "#9a9486", // dim text
  faint: "#b3ad9f", // faint text / disabled-ish

  cardBg: "#fff",
  cardBorder: "#e4e1d8",
  pillBorder: "#e2dfd6",
  divider: "#f2f0e9",
  headerDivider: "#eceae2",

  chipBg: "#efece4",
  chipHover: "#e6e2d6",
  userBubble: "#eeebe2",
  segmentEmpty: "#e8e5dc",
  barTrack: "#ece9e0",
  baselineBar: "#c6c1b2",

  // accent is theme-driven; default below, overridden by Theme.accent at runtime
  accent: "#0e7a70",
  accentDark: "#0a5c54",
  rubricFill: "#b5a684",
  rubricAlt: "#a3927a",
  success: "#2e8b57",
  gap: "#c05840",
  raisedNote: "#b0821f",
  disabledSend: "#d8d4c9",
  checkboxOffBorder: "#d5d1c6",
  rowHover: "#f6f4ee",
} as const;

// Typography sizes (handoff §Typography)
export const T = {
  h1: 27,
  cardTitle: 15,
  body: 14.5,
  bodyL: 15.5,
  eyebrow: 10.5,
  caption: 11.5,
  monospacePct: 11,
} as const;

// Radii (handoff §Radius)
export const R = {
  pill: 26,
  card: 16,
  autocomplete: 14,
  chip: 18,
  preset: 14,
  button: 10,
  stepper: 7,
  checkbox: 5,
  segment: 2.5,
  bar: 4,
  baselineBar: 3.5,
} as const;

// Shadows (handoff §Shadow)
export const SH = {
  pill: "0 1px 3px rgba(28,28,26,.05)",
  dropdown: "0 8px 24px rgba(28,28,26,.09)",
  officialRing: "0 0 0 1.5px rgba(28,28,26,.38)",
} as const;

// Inject design tokens as CSS custom properties on :root. Called once at startup
// from main.tsx; accent is overridable so a theme switch re-calls this.
export function injectThemeCss(theme: Theme): void {
  const root = document.documentElement;
  root.style.setProperty("--t-bg", C.pageBg);
  root.style.setProperty("--t-ink", C.ink);
  root.style.setProperty("--t-accent", theme.accent);
  root.style.setProperty("--t-accent-dark", shade(theme.accent));
}

// Crude darken for hover — only used on the accent link, good enough for a
// single-color brand accent.
function shade(hex: string): string {
  if (!hex.startsWith("#") || hex.length !== 7) return hex;
  const n = parseInt(hex.slice(1), 16);
  const r = Math.max(0, ((n >> 16) & 0xff) - 18);
  const g = Math.max(0, ((n >> 8) & 0xff) - 18);
  const b = Math.max(0, (n & 0xff) - 18);
  return "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
}

// Copy variants by tone (handoff §Landing + §Tweakable variants)
export function copy(tone: Tone) {
  if (tone === "technical") {
    return {
      title: "Request an agent",
      sub: "Free-text task or SkillsFuture role → skill loadout → sandbox-verified benchmark → delivered spec and scorecard.",
      placeholder: "Task description or role name…",
    };
  }
  return {
    title: "What should your agent get done?",
    sub: "Describe the task in plain words, or name a role. Team247 builds the specialist, proves every skill by running real code in a sandbox, and hands you the receipts.",
    placeholder: "Describe a task, or type a role…",
  };
}

export const TASK_CHIPS = [
  "Pull payment terms from vendor contracts into a comparison table",
  "Build a weekly sales dashboard from messy CSV exports",
  "Migrate our reporting off spreadsheets into a proper pipeline",
];

// Role chips map short labels → a catalog search prefix (the prototype resolves
// these against its CATALOG; here we search the live /api/catalog instead).
export const ROLE_CHIPS = [
  "Data Analyst",
  "Data Engineer",
  "DevOps Engineer",
  "Security Architect",
];

export const THREAD_PLACEHOLDER = "Describe another task or role…";
export const HEADER_CAPTION = "SkillsFuture-grounded · sandbox-verified";
