import { PortalRouter } from "../../../portal-router";

export const dynamic = "force-dynamic";

export default function CustomerVmPage() {
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  return <PortalRouter apiBaseUrl={apiBaseUrl} />;
}
