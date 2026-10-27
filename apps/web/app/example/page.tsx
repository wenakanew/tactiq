import type { Metadata } from "next";
import ExampleMatch from "../../components/workspace/ExampleMatch";

export const metadata: Metadata = {
  title: "Interactive synthetic example | Tactiq",
  description: "Explore prepared replay, evidence and personalized explanations without signing in or starting an analysis.",
};

export default function ExamplePage() {
  return <ExampleMatch />;
}
