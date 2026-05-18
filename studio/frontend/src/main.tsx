import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import App from "./App";
import { ToasterProvider } from "./components/Toaster";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Most of our data is short-lived enough that we'd rather show
      // freshness over an aggressive cache. The run-detail page sets its
      // own polling interval.
      staleTime: 5_000,
      refetchOnWindowFocus: false,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToasterProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </ToasterProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
