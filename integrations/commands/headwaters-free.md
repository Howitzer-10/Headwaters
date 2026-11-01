---
description: Allow free cloud and local models only; never a paid key.
---

Call `headwaters_scope` with {"scope": "free"}. Tightening applies at once. If the result says `confirmation_required`, the setting would loosen a lock: tell the user the command in `user_command` and let them run it. Never run it yourself. Then say in one line which scope is now in force. Use `headwaters_do` for implementation work.
