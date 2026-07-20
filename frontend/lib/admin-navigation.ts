export const adminSections = [
  "overview",
  "clusters",
  "vms",
  "access",
  "networks",
  "provisioning",
  "audit",
] as const;

export type AdminSection = (typeof adminSections)[number];

export function sectionFromSearch(
  search: string,
  allowedSections: readonly AdminSection[],
): AdminSection {
  const requested = new URLSearchParams(search).get("section");
  return allowedSections.includes(requested as AdminSection)
    ? requested as AdminSection
    : "overview";
}

export function hrefForSection(currentHref: string, section: AdminSection): string {
  const url = new URL(currentHref);
  if (section === "overview") url.searchParams.delete("section");
  else url.searchParams.set("section", section);
  return `${url.pathname}${url.search}${url.hash}`;
}
