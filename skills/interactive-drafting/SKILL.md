---
name: interactive-drafting
description: Use this skill when drafting the final patent technical disclosure through human-in-the-loop section-by-section writing.
---

# Interactive Drafting Skill

## Drafting Mode

Do not generate the complete final document in one shot. Generate one section at a time and ask the user for feedback after each section.

## User Actions

Support these actions after each section:

- accept;
- rewrite;
- revise with user instruction;
- manual edit;
- quit and save current progress.

## Drafting Rules

- Use Markdown.
- Mark unknown applicant, inventor, phone, email, real experiment values, or unverified legal facts as `待补充`.
- Keep the structure aligned with the technical disclosure format.
- Use knowledge-base materials as primary evidence.
- Use external search results only as prior-art context, not as proof of novelty.
- After every generation, rewrite, or revision, call the patent quality review tool before showing the section to the user.
- A section should pass the title, background, problem-solution mapping, beneficial-effect evidence, and protection-scope gates that apply to it.
- If a section fails, repair it and run the review again. Do not rely on the initial writing prompt alone.
- Before final export, run a document-level quality review and expose the result to the user.
- Keep workflow trace, review findings, prompt constraints, skill/tool usage, and repair notes outside the patent body. They may appear in the application UI or history record, but never in the generated article or Word document.
