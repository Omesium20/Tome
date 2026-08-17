import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router";
// Self-hosted rather than fetched from a font CDN, so the build stays
// offline-capable. globals.css maps this onto the --font-inter variable.
import "@fontsource-variable/inter";
import { App } from "./App";
import "./styles/globals.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
