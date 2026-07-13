---
name: knowledge-graph-design
description: Use this skill whenever knowledge-base materials are uploaded, deleted, refreshed, or visualized. It keeps the application knowledge graph readable, sparse, and useful for patent ideation.
---

# Knowledge Graph Design Skill

## Goal
Generate one clear graph that helps users understand the current knowledge base before patent ideation.

## Graph Rules
- Prefer a layered graph: documents on the left, knowledge-base theme in the center, concepts/problems/methods/metrics on the right.
- Keep the graph sparse: at most 8 document nodes, 10 concept-side nodes, and 28 edges.
- Use short node labels. Document labels should be filenames shortened to two lines. Concept labels should usually be 4-10 Chinese characters.
- Do not render edge labels as visible text when the graph is dense. Put relationship details in SVG tooltips or structured data.
- The visible graph should use only backbone edges: document nodes connect to the central knowledge-base node, and the central node connects to concept nodes. Do not draw every document-to-concept edge directly because it creates unreadable crossings.
- Preserve document-to-concept details as hover text, source lists, or structured metadata rather than visible crossing edges.
- Each document should connect to no more than 3 core concepts.
- Merge duplicate or near-duplicate concepts before display.
- Do not include raw paragraphs, long summaries, API errors, or generated-writing explanations as graph labels.

## Patent Workflow Usage
- Regenerate the graph after every upload, delete, clear, or refresh of the knowledge base.
- Regenerate the graph again at the beginning of patent generation, because the writing workflow must use the current knowledge-base state.
- Use the graph as planning context, not as a replacement for reading LightRAG retrieval results.
