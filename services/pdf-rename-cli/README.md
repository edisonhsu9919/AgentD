# PDF Rename CLI

Managed CLI service for the `pdf-rename` skill.

Commands:

- `pdf-rename-cli health`
- `pdf-rename-cli extract-text --input claim.pdf --output pdf_pages_preview.txt --chars 300`
- `pdf-rename-cli split --plan split_plan.json`

Behavior:

- stdout emits one final JSON payload
- stderr emits incremental progress lines for AgentD Task Output / terminal visibility
- paths are expected to be passed relative to the AgentD session workspace or as workspace-local absolute paths
