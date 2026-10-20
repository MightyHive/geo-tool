/**
 * Personas — AI visibility metrics compared across buyer personas from the wizard.
 */

import { Users } from "lucide-react";
import { filterContextByPersona, listProbePersonas } from "../lib/promptCategoryGrouping";
import { personaCitationVisibilityPoints } from "./CitationVisibilityScatter";
import { DimensionVisibilitySection, type DimensionConfig } from "./DimensionVisibilitySection";
import PersonaOverviewSection from "./PersonaOverviewSection";

const PERSONAS_CONFIG: DimensionConfig = {
  title: "Personas",
  description:
    "Compare AI visibility and share of voice across the buyer personas your persona prompts were written for.",
  dimensionLabel: "Persona",
  scatterSubtitle: "Your brand by buyer persona",
  emptyIcon: Users,
  emptyMessage:
    "No persona prompts in this audit yet. Add buyer personas in the setup wizard (Personas step) to compare visibility by persona.",
  list: listProbePersonas,
  filter: filterContextByPersona,
  points: personaCitationVisibilityPoints,
  renderIntro: (ctx) => <PersonaOverviewSection ctx={ctx} showScatter={false} />,
};

export function PersonasVisibilitySection({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  return <DimensionVisibilitySection auditDirOrSlug={auditDirOrSlug} config={PERSONAS_CONFIG} />;
}
