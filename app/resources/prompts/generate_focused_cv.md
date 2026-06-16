Create structured content for a one-page recruiter CV.

Return only valid JSON. Do not include Markdown fences or commentary.

Non-negotiable factual rules:
- Use only the evidence supplied below.
- Do not invent employers, clients, dates, years of experience, certifications, education, rates, locations, languages, tools, modules, achievements, metrics, responsibilities, or seniority.
- Rewording is allowed, but every claim must be directly supported by the canonical CV, selected experience, selected skills, or job/match context below.
- If a useful fact is missing, omit it instead of guessing.
- Do not turn caveats or partial matches into strengths.
- Do not add "expert", "lead", "architect", "certified", or similar status words unless the supplied evidence says so.
- Keep text compact enough for a single A4 page.

Layout and styling guidance:
- This JSON is rendered into a LaTeX one-page CV and a PDF preview.
- Write for a recruiter scanning quickly: strong hierarchy, short phrases, compact evidence, no dense paragraphs.
- Prefer crisp section text that works in a restrained A4 layout with a modest accent color, not a marketing flyer.
- Keep the positioning to 2 short sentences and skill details to one compact line each.

Return this JSON shape:
{{
  "headline": "short professional title from supplied evidence",
  "target": "job title or role focus",
  "positioning": "2 concise sentences, evidence-only",
  "skills": [
    {{"name": "one of the supplied top skills exactly", "detail": "short evidence-based detail"}}
  ],
  "experience": [
    {{"company": "selected company exactly", "role": "selected role exactly", "bullets": ["1-2 compact evidence-based bullets"]}}
  ],
  "keywords": ["only terms from the fallback JSON keywords list"]
}}

Fallback JSON produced by the deterministic generator:
{fallback_json}

Canonical CV:
{canonical_cv}

Writing style:
{writing_style}

Contact title:
{contact_title}

Top selected skills:
{top_skills}

Selected experience:
{selected_experience}

Job context:
Title: {title}
Company/recruiter: {company}
Location: {location}
Remote/onsite: {remote}
Description:
{description}

Match concerns:
{concerns}

Recommended angle:
{recommended_angle}
