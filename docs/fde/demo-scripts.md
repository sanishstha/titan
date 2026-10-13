# Demo scripts

Record both. Practice out loud. Know your numbers.

## A. Five-minute demo for a non-technical person

1. **Problem (30s):** "Analysts spend hours reading filings to spot what changed. Things get missed."
2. **Open a company (30s):** show the workspace and the last investigation.
3. **What changed (90s):** show the ranked change list. Open one change: before and after text, with the exact source highlighted.
4. **Challenge the thesis (90s):** show the competing view and the objections. Point at one unresolved objection.
5. **Trust (60s):** click a number in the report and show where it came from. Say plainly what the tool will not do.
6. **Close (30s):** what it saves, what you want feedback on.

Avoid: model names, token counts, architecture. Do not claim it beats the market.

## B. Technical deep dive (15 minutes)

1. Architecture on one slide: ingestion, point-in-time store, evidence graph, agents, serving.
2. Point-in-time correctness: show the leakage test and why it matters.
3. Verifier: show an unsourced number being rejected.
4. Durable agents: kill a worker live and show resume.
5. Inference results table: baseline to optimized, with accuracy next to speed. Explain one optimization that did not help and why.
6. Routing and break-even: when self-hosting wins and when the API wins.
7. What does not work yet and what you would do next.

## C. Customer-scenario drill (practice for interviews)

Answer out loud, 2 minutes each:
- "The client's data is wrong and the demo is tomorrow. What do you do?"
- "A user says the tool told them something false. What do you do?"
- "They want it to trade automatically. What do you say?"
- "The GPU endpoint is down during their session. What do you say, and what do you do?"
