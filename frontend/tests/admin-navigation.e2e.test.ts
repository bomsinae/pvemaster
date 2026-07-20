import assert from "node:assert/strict";
import test from "node:test";

import {
  adminSections,
  hrefForSection,
  sectionFromSearch,
} from "../lib/admin-navigation.ts";

test("admin section navigation creates reversible URLs", () => {
  assert.equal(
    hrefForSection("https://pvemaster.example.test/", "clusters"),
    "/?section=clusters",
  );
  assert.equal(
    hrefForSection("https://pvemaster.example.test/?section=clusters", "vms"),
    "/?section=vms",
  );
  assert.equal(
    hrefForSection("https://pvemaster.example.test/?section=vms", "overview"),
    "/",
  );
});

test("admin section navigation restores only permitted sections", () => {
  assert.equal(sectionFromSearch("?section=audit", adminSections), "audit");
  assert.equal(
    sectionFromSearch("?section=audit", ["overview", "clusters", "vms", "access"]),
    "overview",
  );
  assert.equal(sectionFromSearch("?section=unknown", adminSections), "overview");
});
