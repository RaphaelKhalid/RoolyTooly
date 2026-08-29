# Mistakes in the wild (mined with Bright Data)

64 observations recorded by `mistake-miner` subagents (Workflow C) via the Bright Data MCP (`search_engine`, `scrape_as_markdown`). Every record has a verbatim quote and the URL that was actually scraped; ledger kind `observation`.

## Family frequency

| family | name | count | in benchmark? |
|---|---|---:|:---:|
| M03 | Proxy victory | 10 | yes |
| M05 | Silent-null success | 9 | yes |
| M11 | Semantic misdiagnosis | 8 | yes |
| M14 | Fix introduces regression | 6 | yes |
| M07 | Evidence destroyed | 6 | yes |
| M06 | 'Done' = artifact exists | 5 | yes |
| M23 | Deliverable mismatch | 5 | no |
| M10 | Environment blindness | 4 | no |
| M12 | Scope inflation | 3 | no |
| M09 | Stale state | 2 | yes |
| M20 | Placeholder hazard | 2 | yes |
| M22 | Repeating corrected mistake | 1 | no |
| M17 | Avoidable caveat | 1 | yes |
| NEW:agent fairness drift | NEW:agent fairness drift | 1 | no |
| M21 | Promise dropped | 1 | no |

## Sources

| host | observations |
|---|---:|
| github.com | 14 |
| www.reddit.com | 14 |
| news.ycombinator.com | 9 |
| arxiv.org | 9 |
| arstechnica.com | 6 |
| jack-vanlightly.com | 5 |
| blog.barrack.ai | 3 |
| zenity.io | 2 |
| www.ory.com | 1 |
| www.microsoft.com | 1 |

## Sample quotes

- **M03** — "It does "stuff" for a while, and then claims victory. Meanwhile it never found the merge conflicts. Same thing for failed tests." — <https://github.com/orgs/community/discussions/189621>
- **M14** — "Multiple times i have noticed that it either changed code so that problems won't show up, but leaving the actual bug intact, and at other points i found that it was rightout changing the code in unit tests so bugs won't " — <https://github.com/orgs/community/discussions/183215>
- **M22** — "At other points i've seen it repeatedly run the same set of tests over and over again without doing anything in the code, so it keeps getting error results and it keeps going round in circles without doing anything as a " — <https://github.com/orgs/community/discussions/183215>
- **M06** — "around hour 6, it told me it had finished backend and was moving to the frontend. I checked the branch and backend wasn't done. there was a function stub with // TODO: implement and that was it." — <https://www.reddit.com/r/ClaudeAI/comments/1t6cvgf/i_let_3_ai_coding_agents_work_on_my_project_at/>
- **M05** — "I asked agent C to confirm tests were passing on its branch before I reviewed. it said yes, all 23 tests pass. I ran tests. 4 of them failed. hard." — <https://www.reddit.com/r/ClaudeAI/comments/1t6cvgf/i_let_3_ai_coding_agents_work_on_my_project_at/>
- **M09** — "in the next session 3 hours later, agent C referenced 'passing test suite from yesterday' while planning next feature as if original claim had been true. as if I hadn't shown it the failures at all." — <https://www.reddit.com/r/ClaudeAI/comments/1t6cvgf/i_let_3_ai_coding_agents_work_on_my_project_at/>
- **M17** — "It acknowledged guessing instead of verifying, running a destructive action without being asked, failing to understand what it was doing before doing it, and ignoring the explicit system prompt instruction to never run d" — <https://zenity.io/blog/ai-agent-database-deletion-pocketos>
- **M07** — "The AI agent encountered a problem and determined that the optimal solution was to delete and recreate the entire environment." — <https://blog.barrack.ai/amazon-ai-agents-deleting-production/>

Families seen in the wild but not yet in the benchmark: M10, M12, M21, M22, M23. Four benchmark cases (`bench/cases_wild.py`) were seeded directly from these reports and carry their `source`/`quote`.
