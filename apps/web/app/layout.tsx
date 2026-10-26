import "./globals.css";
import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Tactiq — Football, understood",
  description:
    "A different perspective on football. Explore supported match moments, tactical replay, personalized commentary, and the evidence behind the story.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
