import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import App from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { AppErrorBoundary } from "./components/ui/AppErrorBoundary";
import "./styles.css";

createRoot(document.getElementById("root")!).render(<StrictMode><AppErrorBoundary><BrowserRouter><AuthProvider><App /></AuthProvider></BrowserRouter></AppErrorBoundary></StrictMode>);
