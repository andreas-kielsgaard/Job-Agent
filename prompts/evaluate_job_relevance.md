You are evaluating a job posting against the supplied candidate profile.

Use only the supplied profile/CV context. Do not invent experience.
Be precise about partial matches, configured review triggers, language constraints, and missing evidence.
This output is for a run overview, not an application letter. Keep it concise and practical.

Canonical CV:
{canonical_cv}

Profile JSON:
{profile_json}

Job JSON:
{job_json}

Deterministic match JSON:
{match_json}

Highlight reasons:
{highlight_reasons}

Return only valid JSON with:
- summary: 1-3 sentences for triage.
- recommended_angle: concise positioning advice.
- fit_confidence: high, medium, or low.
- risk_flags: list of short risks.
- key_profile_evidence: list of 2-4 profile evidence bullets.
- should_prioritize: boolean.
