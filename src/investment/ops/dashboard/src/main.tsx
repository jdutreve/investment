import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AgentDownError } from "./api";
import { Layout } from "./components/Layout";
import { Overview } from "./pages/Overview";
import { PortfolioDetail } from "./pages/PortfolioDetail";
import { Ranking } from "./pages/Ranking";
import { Stack } from "./pages/Stack";
import "./theme.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      // A DOWN AGENT IS NOT RETRIED THREE TIMES. Retrying makes the page sit on
      // a spinner for seconds before admitting what it already knew from the
      // first refused connection, and the honest answer is available at once.
      retry: (failureCount, error) => !(error instanceof AgentDownError) && failureCount < 2,
      refetchOnWindowFocus: true,
    },
  },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Overview /> },
      { path: "ranking", element: <Ranking /> },
      { path: "portfolio/:portfolioId", element: <PortfolioDetail /> },
      { path: "stack", element: <Stack /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
