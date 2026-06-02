/**
 * Voice+Video+Face: derive UI phase from GET /api/face/session/{id} payload.
 * Kept in sync with sci_fi_assistant.html faceSessionPollTick (single source for tests).
 */
(function (global) {
  "use strict";

  function safeTrim(value) {
    return (typeof value === "string" ? value : "").trim();
  }

  /**
   * @param {Record<string, unknown>} d - session JSON from API
   * @returns {{ multi: boolean, phase: string, primaryName: string, enrolledOnlyMerged: number, multiAllEnrolledStrict: boolean, hasStrictFrame: boolean }}
   */
  function computeFaceSessionPhase(d) {
    const obs = d.observed_person_ids || [];
    const names = d.observed_with_names || [];
    const obsNow = d.observed_now_person_ids;
    const namesNow = d.observed_now_with_names || [];
    const hasStrictFrame = Array.isArray(obsNow) && obsNow.length > 0;
    const multiAllEnrolledStrict =
      hasStrictFrame &&
      obsNow.length >= 2 &&
      namesNow.length === obsNow.length &&
      namesNow.every(function (n) {
        return n && n.enrolled;
      });
    const enrolledOnlyMerged = (names || []).filter(function (n) {
      return n && n.enrolled;
    }).length;
    const multi =
      d.focus_mode === "multiface" ||
      multiAllEnrolledStrict ||
      (!hasStrictFrame && obs.length >= 2 && enrolledOnlyMerged >= 2);
    let phase = "unknown";
    let primaryName = "";
    if (multi) {
      phase = "multi";
    } else if (
      hasStrictFrame &&
      obsNow.length === 1 &&
      namesNow[0] &&
      namesNow[0].enrolled
    ) {
      phase = "single";
      primaryName = safeTrim(namesNow[0].display_name) || "Guest";
    } else if (d.focus_mode === "person" && safeTrim(d.focus_person_id)) {
      const fp = safeTrim(d.focus_person_id);
      const row =
        names.find(function (n) {
          return n.person_id === fp;
        }) ||
        namesNow.find(function (n) {
          return n.person_id === fp;
        });
      if (row && row.enrolled) {
        phase = "single";
        primaryName = safeTrim(row.display_name) || "Guest";
      }
    }
    if (phase === "unknown" && enrolledOnlyMerged === 1) {
      const en = names.find(function (n) {
        return n && n.enrolled;
      });
      if (en) {
        phase = "single";
        primaryName = safeTrim(en.display_name) || "Guest";
      }
    }
    return {
      multi: !!multi,
      phase: phase,
      primaryName: primaryName,
      enrolledOnlyMerged: enrolledOnlyMerged,
      multiAllEnrolledStrict: !!multiAllEnrolledStrict,
      hasStrictFrame: !!hasStrictFrame,
    };
  }

  global.computeFaceSessionPhase = computeFaceSessionPhase;
})(typeof window !== "undefined" ? window : globalThis);
