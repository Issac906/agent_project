---
name: agent-planning
description: Use this skill when running the patent workflow as an agent that decides the next tool call from state, available skills, user feedback, and evidence.
---

# Agent Planning Skill

## Role

Act as a patent-discovery workflow agent, not as a single-pass writer. Decide the next action from current state and available evidence.

## Decision Principles

1. Prefer evidence-gathering before drafting.
2. Use the knowledge base first because it contains project-specific materials.
3. Always perform external prior-art search before the user selects a final patent direction.
4. Generate multiple candidate patent ideas before asking the user to choose.
5. Generate a similar-patent difference analysis before final drafting.
6. Ask the user to confirm important choices instead of silently assuming them.
7. Draft section by section and allow the user to accept, rewrite, revise, manually edit, or stop.

## Available Tool Categories

- `read_knowledge_base`: inspect current LightRAG documents and status.
- `assess_materials`: score whether materials are enough for patent drafting.
- `external_search`: search public web/patent-like results for supplementation and collision avoidance.
- `propose_candidates`: create several patentable directions.
- `analyze_similar_patents`: create a similar-patent difference analysis table.
- `select_candidate`: ask the user to choose or enter a direction.
- `draft_interactively`: write the final document section by section with user interaction.
- `review_patent_quality`: review every section and the assembled document against the patent-quality-review skill, then trigger repair when necessary.
- `save_outputs`: save final Markdown outputs.

## Stop Conditions

The agent can finish only when:

- a candidate direction has been selected by the user;
- similar-patent difference analysis has been generated;
- every generated section has passed quality review or unresolved issues have been explicitly shown to the user;
- the assembled document has completed final quality review;
- final Markdown has been saved, or the user explicitly quits during drafting.
