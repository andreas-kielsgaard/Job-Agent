from __future__ import annotations

TEMPLATE_VARIABLES = {
    "job": "The parsed job object, e.g. job.title, job.company, job.location, job.application_url.",
    "match": "Internal match result, e.g. match.reasons, match.concerns, match.recommended_angle. Avoid scores in recruiter-facing templates.",
    "contact": "Your profile contact fields, e.g. contact.name, contact.email, contact.linkedin.",
    "availability": "Availability fields from profile/preferences.yaml.",
    "location_policy": "Relocation and preferred-location fields from profile/preferences.yaml.",
    "top_skills": "Exactly five selected skills for the role.",
    "selected_experience": "The selected relevant experience entries.",
    "application_examples": "Relevant human-edited application examples selected for the role.",
    "keyword_line": "Additional keywords selected for this role.",
    "application_text": "Only available in form-answers template.",
}
