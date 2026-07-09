# Matching and AI review notes

Deterministic scoring remains stored separately. When LLM-enhanced matching is enabled, the AI match score is also stored separately and job views sort/display the average of deterministic and AI scores.

## Deterministic matching

- Keyword groups are unified. A row has alternatives, a mode, a proficiency value, and optional years of evidence.
- `main` rows represent proficiency. If several main rows match, their proficiency values are averaged.
- `bonus` rows boost the proficiency score. If several bonus rows match, only the highest boost is used. The resulting score can exceed 100.
- `detractor` rows reduce the proficiency score. If several detractors match, only the largest detractor is used.
- Employment type, remote setup, location, contract length, compensation, and language requirements are employment conditions. They filter or flag jobs but do not change the deterministic match score.
- Target roles, role aliases, and role interests remain profile context and can feed highlights or AI review.
- Caveat review triggers add concerns and optional AI review triggers without directly reducing score.

## AI relevance review

LLM-enhanced matching runs after deterministic scoring for promising postings that have description text. It asks for an advisory match score, employment-condition values, summary, risks, and profile evidence. Excluded jobs stay skipped unless the profile explicitly allows excluded postings with review triggers.

## Language policy

With a configured language policy, mandatory language requirements can either add a penalty or exclude the posting when a required language is not listed as acceptable or fluent.

## Application examples

Human-edited application examples are selected by linked skills, modules, roles, and job context. Relevant examples are included in Claude application prompts, AI edit context, and external review bundles.
