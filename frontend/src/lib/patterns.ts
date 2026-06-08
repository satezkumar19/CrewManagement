/**
 * Client-side Pattern Detection aggregation (L4 #4) — mirrors the backend
 * pattern_service so the panel can build the report INCREMENTALLY from whatever
 * subset of decisions has been revealed so far (the walkthrough reveals them one
 * crew member at a time). Same categories / thresholds as the server, so a full
 * pass yields the same result as GET /patterns.
 */
import type { DecisionTrace, PatternReport, PatternCategory } from "@/types";

// A category is "recurring" once it blocks this many DISTINCT decisions.
const RECURRING_THRESHOLD = 2;

interface Rule {
  category: string;
  keywords: string[];
  label: string;
  recommendation: string;
}

// ORDER MATTERS: specific categories before the generic "certificate" catch-all,
// so "STCW certificate" → stcw and "Medical certificate" → medical.
const RULES: Rule[] = [
  {
    category: "visa",
    keywords: ["visa", "c1/d", "c1 d", "port restriction", "port entry", "entry permit", "transit"],
    label: "Visa / port-entry compliance",
    recommendation: "Pre-validate visa & port-entry requirements during matching, before the compliance gate.",
  },
  { category: "stcw", keywords: ["stcw"], label: "STCW certification",
    recommendation: "Track STCW expiry in the crew pool and surface lapses at match time." },
  { category: "medical", keywords: ["medical", "fit-to-sail", "fit to sail"], label: "Medical certificate",
    recommendation: "Add a medical-validity check to the matching pre-filter." },
  { category: "passport", keywords: ["passport"], label: "Passport validity",
    recommendation: "Flag passports expiring within the contract window before selection." },
  { category: "training", keywords: ["proficiency", "survival craft", "basic safety", "firefighting", "drill"],
    label: "Training / proficiency",
    recommendation: "Verify required proficiency certificates for the role during matching." },
  { category: "certification", keywords: ["certificate", "certification", "endorsement"],
    label: "Other certification",
    recommendation: "Audit the certificate set against the vessel/role requirements earlier." },
];

const LABELS: Record<string, string> = { other: "Other" };
const RECS: Record<string, string> = { other: "Review the recurring failure reasons and add an earlier guardrail." };
RULES.forEach((r) => { LABELS[r.category] = r.label; RECS[r.category] = r.recommendation; });

const META_PREFIXES = ["all ", "no candidate"];

function categorize(reason: string): string {
  const text = (reason || "").toLowerCase();
  for (const r of RULES) {
    if (r.keywords.some((k) => text.includes(k))) return r.category;
  }
  return "other";
}

interface Bucket {
  category: string;
  label: string;
  decisions: Set<string>;
  occurrences: number;
  ports: Set<string>;
  ranks: Set<string>;
  examples: string[];
}

export function buildPatternReport(decisions: DecisionTrace[]): PatternReport {
  const total = decisions.length;
  const signed = decisions.filter((d) => d.outcome_status === "signed_on").length;
  const rejected = decisions.filter((d) => d.outcome_status === "rejected").length;
  const pending = decisions.filter((d) => d.outcome_status === "pending").length;

  const agg = new Map<string, Bucket>();
  const bucket = (cat: string): Bucket => {
    let b = agg.get(cat);
    if (!b) {
      b = { category: cat, label: LABELS[cat] || cat, decisions: new Set(), occurrences: 0, ports: new Set(), ranks: new Set(), examples: [] };
      agg.set(cat, b);
    }
    return b;
  };

  for (const d of decisions) {
    const dep = d.query_context?.departing_crew || {};
    const port = dep.port;
    const rank = dep.rank;

    const reasons: string[] = [];
    for (const att of d.attempts || []) reasons.push(...(att.failures || []));
    if (d.outcome_status === "rejected") reasons.push(...(d.outcome_reasons || []));

    for (const r of reasons) {
      if (!r || META_PREFIXES.some((p) => r.trim().toLowerCase().startsWith(p))) continue;
      const b = bucket(categorize(r));
      b.occurrences += 1;
      b.decisions.add(d.decision_id);
      if (port) b.ports.add(port);
      if (rank) b.ranks.add(rank);
      if (!b.examples.includes(r) && b.examples.length < 4) b.examples.push(r);
    }
  }

  const categories: PatternCategory[] = [...agg.values()]
    .map((b) => ({
      category: b.category,
      label: b.label,
      decisions_affected: b.decisions.size,
      occurrences: b.occurrences,
      ports: [...b.ports].sort(),
      ranks: [...b.ranks].sort(),
      examples: b.examples,
    }))
    .sort((a, b) => b.decisions_affected - a.decisions_affected || b.occurrences - a.occurrences);

  const top = categories[0];
  const recurring_gap =
    top && top.decisions_affected >= RECURRING_THRESHOLD
      ? { ...top, recommendation: RECS[top.category] || RECS.other }
      : null;

  return {
    summary: {
      total, signed_on: signed, rejected, pending,
      rejection_rate: total ? Math.round((rejected / total) * 1000) / 10 : 0,
    },
    categories,
    recurring_gap,
    generated_at: "",
  };
}
