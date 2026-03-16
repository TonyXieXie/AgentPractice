import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import GraphStudioWindow from "./GraphStudioWindow";
import WorkDirWindow from "./WorkDirWindow";
import { resolveApiBaseUrl } from "./shared/api/base";

const params = new URLSearchParams(window.location.search);
const windowKind = params.get("window");
const Root =
  windowKind === "workdir"
    ? WorkDirWindow
    : windowKind === "graph-studio"
      ? GraphStudioWindow
      : App;

const bootstrap = async () => {
  await resolveApiBaseUrl();
  ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <Root />
    </React.StrictMode>,
  );
};

void bootstrap();
