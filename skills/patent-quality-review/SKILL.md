---
name: patent-quality-review
description: Use this skill after generating or revising every patent section and before final export. It enforces stable patent-writing quality regardless of knowledge-base topic or source material.
---

# Patent Quality Review Skill

## Role

Act as a mandatory reviewer, not as an optional writing suggestion. Every generated or revised section must be checked before it is shown to the user. The assembled document must be checked again before export.

## Section Acceptance Gates

### Title Page and Invention Name

- Use a direct invention-object title.
- Do not use the pattern `基于……的……`.
- Keep the title within 30 Chinese characters where practical.
- Put algorithms, features, model structures, indicators, and process details in the invention content, not in the title.
- Example:
  - Reject: `基于VMD频带能量特征与时序网络的铝电解槽阳极效应早期预警方法`
  - Accept: `铝电解槽阳极效应早期预警方法`

### Background Technology

- Limit the section to 2-3 concrete industry problems directly addressed by the invention.
- Do not write a broad industry history or generic market introduction.
- Do not use `技术空白一/二/三`, `填补技术空白`, `行业尚无`, or similar absolute claims.
- Describe limitations through concrete application scenario, input data, constraints, control mechanism, evaluation mechanism, or feedback loop.

### Invention Content

- Start with `关键创新点`.
- Then include `发明目的`.
- Then include `拟解决的技术问题`.
- Each background problem must map to one corresponding technical problem and solution target.
- Do not repeat a separate `区别于现有技术的关键创新点` section later.
- Technical solutions must be specific to the current knowledge-base industry and object; reject generic placeholders such as `目标行业` or `目标对象`.

### Beneficial Effects

- Quantified effects are allowed only when the knowledge-base or retrieved evidence explicitly supports the same number.
- Reject invented percentages, amounts, accuracy improvements, cost savings, or duration reductions.
- Without evidence, use concise qualitative effects and state that quantitative validation is pending.

### Protection Scope

- Explain protection boundaries for the method and system.
- Include device/equipment/storage-medium forms when technically applicable.
- Protect the combination of core technical features, not only an algorithm name.

### No Process Metadata in Patent Body

- The final patent body must contain only patent-topic content.
- Reject any text that describes generation process, self-checks, repair notes, prompt rules, skill/tool usage, backend implementation, or phrases such as `未使用某句式` and `使用了某规则`.
- Quality review findings may be shown in the application UI or history record, but must not be embedded inside the patent article or Word export.

## Repair Procedure

When a section fails:

1. List each failed gate and the exact text causing the failure.
2. Rewrite only the current section.
3. Preserve verified technical facts from the knowledge base.
4. Remove unsupported claims instead of inventing evidence.
5. Re-run the same gates.
6. Show the section to the user only after it passes or after two repair attempts, with unresolved issues clearly displayed.

## Final Document Gate

Before export, verify:

- all required sections exist;
- the title is direct and concise;
- `技术交底书` is not repeated as multiple document titles;
- background problems and invention solutions correspond;
- innovation points are not repeated;
- beneficial effects contain no unsupported quantified claims;
- protection scope is present;
- industry and technical objects are specific rather than generic placeholders.
- no generation-process, self-check, prompt, skill/tool, backend, or repair notes remain in the patent body.
