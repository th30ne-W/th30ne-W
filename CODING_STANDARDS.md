# Coding standards

Read by reviewers. CI enforces what it can; the rest is judgement.

## Tests

- **Test what a viewer sees.** Assertions on a rendered SVG use what it shows: text, geometry (positions, sizes, order), and the styles that actually apply to an element. They never key on class names, wrapper elements, or the renderer's own constants and layout tables.
- **Expected values come from the spec or the fixture**, never from re-running the renderer's arithmetic in the test.
- **Public seams only.** Tests import names listed in a module's `__all__` and drive the CLI as a subprocess (`python -m generator …`). `tests/test_seams.py` enforces the imports.
