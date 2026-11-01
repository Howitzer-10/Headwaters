---
description: Let Headwaters pick the best allowed model (your keys, then free, then local).
---

Call `headwaters_scope` with {"scope": "auto"}. Tightening applies at once. If the result says `confirmation_required`, the setting would loosen a lock: tell the user the command in `user_command` and let them run it. Never run it yourself. A Private lock or a project rule can still narrow it. Then say in one line which scope is now in force.
