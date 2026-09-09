---
id: atoms-fd51ed
title: "Published repository URLs point at atoms-dev/core, which does not exist"
status: todo
priority: 2
size: xs
created: 2026-09-09T21:29:50Z
updated: 2026-09-09T21:29:50Z
depends: []
tags: [hygiene]
---

python/pyproject.toml [project.urls] names github.com/atoms-dev/core for Homepage, Repository, and Issues. That repository does not exist: the URL returns a hard 404, and the atoms-dev account holds no repositories at all. So all three links on the verifiably-atoms PyPI page are dead as shipped in 0.1.0. The code is at github.com/verifiably/atoms.

nodes had the same shape and does not need this fix, which is worth recording so the two are not conflated: github.com/nodes-dev/core returns 301 to github.com/verifiably/nodes, so nodes-core 0.1.1's links still resolve, and nodes' own pyproject.toml was already corrected. atoms inherits no redirect because atoms-dev/core was never a repository that moved.

PyPI metadata is immutable per release, so editing pyproject.toml corrects the tree but the published 0.1.0 page keeps the dead links until a further release goes out. Three one-line edits, no behaviour change: this should ride whatever release comes next rather than justify one on its own.

docs/plans/2026-07-23-plan-a1-core-model.md lines 120-122 carry the same URLs. That is a historical record of what was written at the time and should stay as it is.
