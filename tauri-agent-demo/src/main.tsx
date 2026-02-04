import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import WorkDirWindow from "./WorkDirWindow";

const params = new URLSearchParams(window.location.search);
const isWorkdirWindow = params.get("window") === "workdir";
const Root = isWorkdirWindow ? WorkDirWindow : App;

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
