# Third-party notices

internsHELPer includes the following third-party files. These notices cover those files only; no
license has been selected for the original internsHELPer source code.

| Component | Version | Included files | License | Upstream |
|---|---:|---|---|---|
| htmx | 2.0.8 | `internshelper/web/static/vendor/htmx.min.js` | [Zero-Clause BSD](internshelper/web/static/vendor/licenses/HTMX-0BSD.txt) | [bigskysoftware/htmx](https://github.com/bigskysoftware/htmx/tree/v2.0.8) |
| Alpine.js | 3.15.1 | `internshelper/web/static/vendor/alpine.min.js` | [MIT](internshelper/web/static/vendor/licenses/Alpine-MIT.txt) | [alpinejs/alpine](https://github.com/alpinejs/alpine/tree/v3.15.1) |
| Alpine Collapse | 3.15.1 | `internshelper/web/static/vendor/alpine-collapse.min.js` | [MIT](internshelper/web/static/vendor/licenses/Alpine-MIT.txt) | [alpinejs/alpine](https://github.com/alpinejs/alpine/tree/v3.15.1/packages/collapse) |
| SortableJS | 1.15.6 | `internshelper/web/static/vendor/sortable.min.js` | [MIT](internshelper/web/static/vendor/licenses/Sortable-MIT.txt) | [SortableJS/Sortable](https://github.com/SortableJS/Sortable/tree/1.15.6) |
| Geist | Version not recorded when vendored | `internshelper/web/static/fonts/geist-*.woff2` | [SIL Open Font License 1.1](internshelper/web/static/vendor/licenses/Geist-OFL-1.1.txt) | [vercel/geist-font](https://github.com/vercel/geist-font) |
| pstack-codex | 0.1.0+codex.20260827232005, based on pstack 0.14.5 | `.agents/vendor/pstack-codex/` and adapted skills under `.agents/skills/` | [MIT](.agents/vendor/pstack-codex/LICENSE) | [cursor/plugins pstack](https://github.com/cursor/plugins/tree/main/pstack) |

The JavaScript versions come from `internshelper/web/static/vendor/VERSIONS.txt`. The Geist files
were already present without a recorded release number, so this notice does not invent one. Their
SHA-256 hashes at the time of this notice are:

```text
40073e90816315c92e4f4381bd50b6fdc950b22b0dd010a4179046cf588d4f12  geist-500.woff2
9d99fbd791968493fa507ac846de561cee47b00f8100c23cad333b3cb78392d6  geist-600.woff2
3d59addfafec3452d44717aa0a18911d5c3812487ed06af47f2ef27192ea1eed  geist-700.woff2
```

The license texts were checked against the upstream projects on 2026-09-10. Preserve this file and
the linked license files when redistributing the vendored assets.

## Test fixtures

Files under `tests/fixtures/` are frozen, reduced responses from public job-board APIs and public
community job lists. They exist to test parsers without making network requests. Company and
product names remain the property of their owners; inclusion does not imply endorsement. When
adding a fixture, remove personal applicant data, secrets, tracking parameters, and content that is
not needed to reproduce the parser contract.
