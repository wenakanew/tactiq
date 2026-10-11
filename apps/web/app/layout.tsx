import "./globals.css";
import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Tactiq — Match Intelligence | Premier League × Microsoft",
  description:
    "Real-time agentic football intelligence: evidence-backed insights, dual AI commentary, and chaptered recaps. AI explains the game — it never makes up the game.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
