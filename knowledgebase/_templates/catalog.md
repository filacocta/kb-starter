---
author: kb-bot
created: YYYY-MM-DD
modified: YYYY-MM-DD
type: catalog
topic: projects
title: "[Entity Type] Catalog — [Project Name]"
description: "Living inventory of [entity type] instances for [project name]: what's implemented, what needs work, and what's on the wishlist."
entity_type: "<card | creature | effect | deck | scenario | item | level | ability | ...>"
project: "<project-slug>"
tags: ["<project-slug>", "catalog", "<entity-type>"]
related:
  - "[[knowledgebase/projects/<project-slug>/_summary]]"
  - "[[knowledgebase/projects/<project-slug>/notes/entity-<type>]]"
---

# [Entity Type] Catalog — [Project Name]

One or two sentences orienting the reader: what kind of entities this catalog covers, how they're used in the project, and what the current library looks like at a glance.

---

## ✅ Implemented

Entries that exist in the codebase/seed data and function as intended. Each entry describes what the entity *does* and why it matters — not just its field values.

### [Entity Name] (`entity_id`)

One sentence summary of what this entity does.

**Design notes:** Any interactions, edge cases, balance observations, or strategic role worth capturing for future reference. If there's nothing unusual, omit this subsection.

---

*(repeat for each implemented entry — group into subsections if helpful, e.g. ## Attack Cards, ## Boss Creatures)*

---

## ⚠️ Needs Adjustment

Entries that exist but have known issues: bugs, balance problems, missing features, or design gaps that should be addressed.

### [Entity Name] (`entity_id`)

**Issue:** Plain statement of what's wrong or incomplete.

**Proposed fix / open question:** What change would address this, or what question needs answering before the fix can be designed. Flag with `?` if genuinely open.

---

## 💡 Ideas — To Add

Free-form planning scratchpad. Jot ideas here — no spec required. A concept + rough mechanic is enough to preserve the intent for a future planning session. The agent will help refine these into full specs when the time comes.

Entries can be as rough as a single sentence. Priority is optional but helpful for triage.

### [Idea Name]

**Concept:** What it would do, in one to three sentences.

**Priority:** high / medium / low / someday

**Design notes:** *(optional)* Any rough thoughts on implementation, balance, or how it fits into the broader system.

---

*(Use this section freely — the user populates it, the agent helps refine it)*
