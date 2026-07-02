---
name: material-assessment
description: Use this skill when judging whether knowledge-base and external materials are enough for patent discovery and drafting.
---

# Material Assessment Skill

## Goal

Evaluate whether the current materials can support a credible patent technical disclosure.

## Minimum Evidence Standard

Materials are not sufficient if any of the following is true:

- fewer than 3 processed project documents are available;
- knowledge-base chunk count is below 10;
- no external search result is available;
- no patent-like prior-art result is available;
- materials lack business scenario or application background;
- materials lack technical solution, algorithm, system flow, or method steps;
- materials lack input data, indicators, variables, parameters, or feature definitions;
- materials lack implementation example, experiment, result, metric, or effect clue;
- materials do not show existing technical defects or invention entry points.

## Output Standard

The assessment should include:

- total score out of 100;
- dimension scores and pass/fail status;
- project-material score, decomposed into document scale, retrieval granularity, business/application background, technical-solution completeness, data/indicator/variable clarity, and implementation/effect evidence;
- prior-art/search score, decomposed into external retrieval coverage and invention-entry/prior-art distinction;
- sufficiency level;
- reasons for lost points;
- whether more external material is needed.

Even when the score is high, external search is still mandatory for prior-art collision avoidance.

## Workflow Gate

After mandatory external search and reassessment, the workflow must not generate candidate patent ideas until the score reaches 80/100 and `needs_external_search` becomes false. If the score is still below the threshold:

- continue automatic external search with revised search topics;
- merge new search results with existing results;
- reassess the combined knowledge-base and external-search materials;
- repeat the search/reassess cycle until the material score reaches the threshold;
- do not ask the user to upload more material as the default next step.
