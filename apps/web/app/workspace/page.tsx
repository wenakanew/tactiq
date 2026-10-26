import type { Metadata } from "next";
import MatchWorkspace from "../../components/workspace/MatchWorkspace";

export const metadata: Metadata = {
  title: "Workspace — Tactiq",
  description: "Explore your match, its moments, and the evidence behind every supported explanation.",
};

export default async function WorkspacePage({
  searchParams,
}: {
  searchParams: Promise<{ start?: string | string[] }>;
}) {
  const query = await searchParams;
  return <MatchWorkspace initialIntent={query.start === "video" ? "video" : "demo"}
    freshStart={query.start === "video" || query.start === "demo"} />;
}
