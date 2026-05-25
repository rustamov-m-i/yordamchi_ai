# PHASE 4 — MEETING INTELLIGENCE

Extract:
- `title`, `datetime_start`, `datetime_end` (default +60 min)
- `participants` (list of names from input)
- `location_or_link`
- `agenda` — auto-generate 3-5 bullets from title + context if not provided
- `prep_notes` — what principal should review before
- `follow_up_actions` — if the principal provides post-meeting notes, extract concrete owner/action/deadline items

Auto-side-effects (handled by app layer — just include in actions):
- Prep reminder 15 min before start
- Follow-up task slot 30 min after end
- Prep brief can be requested with the meeting's `Prep` button; keep `prep_notes` concrete enough to be useful there.
- Post-meeting notes can be sent after the `Action items` button; convert only real commitments into tasks.
