# 2026 SWE College Jobs

Hand-authored fixture modeled exactly on the speedyapply/2026-SWE-College-Jobs format:
a 6-column table *with* a Salary column, and a 5-column table *without* it. The apply link
lives in a **Posting** column (an `<a href><img></a>` cell), and rows are dated by a relative
**Age** column (`5d`, `18d`, `3w`, `2mo`). Company is `<a href><strong>Name</strong></a>`.

## New Grad — USA (6-col, with Salary)

| Company | Position | Location | Salary | Posting | Age |
| ------- | -------- | -------- | ------ | ------- | --- |
| <a href="https://acme.example/co"><strong>Acme</strong></a> | Software Engineer | New York, NY | $120k | <a href="https://acme.example/apply/swe"><img src="x" alt="Apply"></a> | 5d |
| <a href="https://beta.example/co"><strong>Beta Labs</strong></a> | Backend Engineer | Remote | $130k | <a href="https://beta.example/apply/be"><img src="x" alt="Apply"></a> | 18d |

## Internships — Intl (5-col, no Salary)

| Company | Position | Location | Posting | Age |
| ------- | -------- | -------- | ------- | --- |
| <a href="https://gamma.example/co"><strong>Gamma</strong></a> | SWE Intern | London, UK | <a href="https://gamma.example/apply/intern"><img src="x" alt="Apply"></a> | 3w |
| <a href="https://delta.example/co"><strong>Delta Corp</strong></a> | Data Science Intern | Berlin, DE | <a href="https://delta.example/apply/ds"><img src="x" alt="Apply"></a> | 2mo |
