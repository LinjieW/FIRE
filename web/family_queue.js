/* family_queue.js — derived parent-identity review queue, with no stored state. */
(function (global) {
  "use strict";

  const CATEGORY_ORDER = Object.freeze([
    "contradiction", "stale", "match", "na"
  ]);

  function canEvaluate(link) {
    return !link.ended_at
      && link.household_status === "active"
      && link.parent_status === "active";
  }

  function categoryFor(evidence) {
    if (evidence.freshness !== "current") return "stale";
    if (!evidence.applicable) return "na";
    if (evidence.finding === "contradiction") return "contradiction";
    if (evidence.finding === "match") return "match";
    return "na";
  }

  function evidenceItem(link, evidence, detector, rootEvaluationId) {
    return {
      category: categoryFor(evidence),
      detector,
      link_id: link.link_id,
      evaluation_id: rootEvaluationId,
      evidence_id: detector === "sex"
        ? evidence.sex_evaluation_id : evidence.evaluation_id,
      household_display_name: link.household_display_name,
      parent_display_name: link.parent_display_name,
      parent_slot_label: evidence.parent_slot_label || null,
      freshness: evidence.freshness,
      applicable: Boolean(evidence.applicable),
      finding: evidence.finding || null,
      delta_years: evidence.delta_years == null ? null : evidence.delta_years,
      reason_code: evidence.reason_code || "finding_unavailable",
      reason: evidence.reason || "the evidence has no usable finding",
      actionable: canEvaluate(link),
      synthetic: false,
    };
  }

  function missingItem(link, detector, rootEvaluationId, reasonCode) {
    return {
      category: "na",
      detector,
      link_id: link.link_id,
      evaluation_id: rootEvaluationId || null,
      evidence_id: null,
      household_display_name: link.household_display_name,
      parent_display_name: link.parent_display_name,
      parent_slot_label: null,
      freshness: "not_evaluated",
      applicable: false,
      finding: null,
      delta_years: null,
      reason_code: reasonCode,
      reason: reasonCode === "sex_not_recorded"
        ? "this older age evaluation has no recorded sex evidence"
        : "this relationship has not been evaluated",
      actionable: canEvaluate(link),
      synthetic: true,
    };
  }

  function project(links) {
    const groups = { contradiction: [], stale: [], match: [], na: [] };
    (Array.isArray(links) ? links : []).forEach(link => {
      const evaluations = Array.isArray(link.evaluations)
        ? link.evaluations : [];
      if (!evaluations.length) {
        groups.na.push(missingItem(link, "evaluation", null, "not_evaluated"));
        return;
      }
      evaluations.forEach(evaluation => {
        const age = evidenceItem(
          link, evaluation, "age", evaluation.evaluation_id);
        groups[age.category].push(age);
        if (evaluation.sex_evaluation) {
          const sex = evidenceItem(
            link, evaluation.sex_evaluation, "sex", evaluation.evaluation_id);
          groups[sex.category].push(sex);
        } else {
          groups.na.push(missingItem(
            link, "sex", evaluation.evaluation_id, "sex_not_recorded"));
        }
      });
    });
    return { category_order: CATEGORY_ORDER.slice(), groups };
  }

  global.FIREFamilyQueue = Object.freeze({ project, categoryFor });
})(window);
