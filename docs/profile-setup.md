# Profile setup notes

The app uses one local profile at a time. A cloned copy can be configured for a different person by editing the private `profile/` folder or, preferably, the Profile page.

## Main profile files

- `profile/contact.yaml`: contact details used in generated CVs, application text, and form answers.
- `profile/preferences.yaml`: availability, location preferences, deterministic matching policy, AI review policy, language policy, and highlighting.
- `profile/skills.yaml`: skills, modules or domains, target roles, caveats, and role aliases.
- `profile/experience.yaml`: case studies and project evidence.
- `profile/canonical-cv.md`: plain-text CV narrative used as factual evidence.
- `profile/writing-style.md`: writing preferences for generated text.
- `profile/application-examples.yaml`: human-edited application examples used as writing and positioning context.

## Editing guidance

Prefer the Profile page for normal changes. It preserves unrelated YAML and keeps matching terms connected to the visible profile entries.

Use Advanced only when you need exact YAML or template control. Private profile files are ignored by Git; `profile.example/` is only the neutral starter profile for new clones.
