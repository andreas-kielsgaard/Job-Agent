# Matching and AI review notes

Deterministic scoring is the source of truth. AI review can summarize, flag risks, and suggest prioritization, but it does not silently replace the score.

## Deterministic matching

- Technical keyword groups add points or exclude postings when marked required.
- Module keyword groups work the same way, but are kept separate because some profiles use domain or platform modules heavily.
- Contract and location policies handle remote preference, permanent-role handling, and visible contract terms.
- Target roles, role aliases, and role interests add a smaller role-interest signal.
- Caveat review triggers add concerns and optional AI review triggers without directly reducing score.

## AI relevance review

AI review can run when configured categories, score thresholds, highlights, caveat triggers, or low source confidence indicate that a posting needs interpretation. Excluded jobs stay skipped unless the profile explicitly allows excluded postings with review triggers.

## Language policy

With a configured language policy, mandatory language requirements can either add a penalty or exclude the posting when a required language is not listed as acceptable or fluent.

## Application examples

Human-edited application examples are selected by linked skills, modules, roles, and job context. Relevant examples are included in Claude application prompts, AI edit context, and external review bundles.
