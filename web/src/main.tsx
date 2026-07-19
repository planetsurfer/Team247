import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { injectThemeCss, defaultTheme } from "./theme";
import "./styles.css";

// Inject design-token CSS custom properties once on startup. App can override
// the accent / copy / suggestions via props later, but the base sheet is here.
injectThemeCss(defaultTheme);

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root not found");
createRoot(rootEl).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
