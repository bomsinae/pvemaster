import { ConsoleWindow } from "./console-window";

export const dynamic = "force-dynamic";

export default async function ConsolePage({
  params,
}: {
  params: Promise<{ workloadId: string }>;
}) {
  const { workloadId } = await params;
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  return <ConsoleWindow apiBaseUrl={apiBaseUrl} workloadId={workloadId} />;
}
