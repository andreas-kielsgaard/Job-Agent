from __future__ import annotations

CLAUDE_MODELS = [
    {
        "label": "Balanced default",
        "value": "claude-sonnet-4-0",
        "quality": "High performance",
        "speed": "Medium",
        "price": "Medium",
        "help": "Auto-updating Sonnet 4 alias. Good default balance of quality, cost, and availability.",
    },
    {
        "label": "Stable balanced",
        "value": "claude-sonnet-4-20250514",
        "quality": "High performance",
        "speed": "Medium",
        "price": "Medium",
        "help": "Pinned Sonnet 4 snapshot. Best if you want reproducible behavior.",
    },
    {
        "label": "Highest performance",
        "value": "claude-opus-4-1-20250805",
        "quality": "Highest performance",
        "speed": "Low",
        "price": "High",
        "help": "Most capable listed model, usually higher cost and slower.",
    },
    {
        "label": "Cheapest and fastest",
        "value": "claude-3-5-haiku-latest",
        "quality": "Basic performance",
        "speed": "High",
        "price": "Low",
        "help": "Auto-updating Haiku 3.5 alias. Fast and cheaper, but weaker for nuanced writing.",
    },
]

TEMPLATE_VARIABLES = {
    "job": "The parsed job object, e.g. job.title, job.company, job.location, job.application_url.",
    "match": "Internal match result, e.g. match.reasons, match.concerns, match.recommended_angle. Avoid scores in recruiter-facing templates.",
    "contact": "Your profile contact fields, e.g. contact.name, contact.email, contact.linkedin.",
    "availability": "Availability fields from profile/preferences.yaml.",
    "location_policy": "Relocation and preferred-location fields from profile/preferences.yaml.",
    "top_skills": "Exactly five selected skills for the role.",
    "selected_experience": "The selected relevant experience entries.",
    "keyword_line": "Additional SAP keywords selected for this role.",
    "application_text": "Only available in form-answers template.",
}
