---
name: formula-formatting
description: Use this skill whenever patent drafting contains equations, variables, objective functions, constraints, evaluation metrics, or algorithm definitions. It standardizes formulas for web and Word output.
---

# Formula Formatting Skill

## Required Source Format

- Write every formula in standard LaTeX.
- Use `$...$` only for short inline variables or expressions.
- Use a standalone `$$...$$` block for numbered, multi-line, fraction, summation, optimization, matrix, or constraint formulas.
- Never place formulas in Markdown code fences.
- Never use pseudo-formula text such as `x_i^2 / sum`, raw Unicode superscripts, or escaped JSON fragments.

## Standard Examples

Inline:

```text
状态变量记为 $x_t$，控制变量记为 $u_t$。
```

Display:

```text
$$
J(\theta)=\sum_{t=1}^{T}\left\|y_t-\hat{y}_t\right\|_2^2+\lambda\|\theta\|_2^2
$$
```

Fraction:

```text
$$
\eta=\frac{Q_{\mathrm{effective}}}{Q_{\mathrm{input}}}
$$
```

Constraint:

```text
$$
\begin{aligned}
\min_{\mathbf{u}}\quad & J(\mathbf{u}) \\
\mathrm{s.t.}\quad & \mathbf{u}_{\min}\leq\mathbf{u}\leq\mathbf{u}_{\max}
\end{aligned}
$$
```

## Variable Definitions

- Define every symbol immediately after the formula.
- State units where applicable.
- Keep symbol meaning consistent across all sections.
- Do not introduce a formula unless the knowledge-base material supports the variables, mechanism, or metric.

## Output Requirements

- Web output must be renderable by MathJax.
- Word output must use native Word equations when conversion succeeds.
- Formula delimiters must be balanced.
- The final document must not contain replacement characters, broken backslashes, raw JSON escapes, or formula code fences.
