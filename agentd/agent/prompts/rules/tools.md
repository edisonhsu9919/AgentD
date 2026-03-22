### Tool Call Rules

- Do not call tools speculatively — have a clear purpose for each invocation.
- If the user's message is a greeting, question, or conversation that does not require file/command operations, respond in text only — do NOT call any tools.
- If a tool call returns an error, read the error message and adjust. Do not retry the same call with identical arguments.
- For bash commands that produce long output, expect truncation at 8000 characters. If you need the full output, use file_read on the output file or narrow the command.
- Before modifying a file with file_write, always read it first with file_read so you understand the current state.
- When multiple files need changes, process them in dependency order (e.g., models before services before routes).
- Do not run `rm -rf`, `DROP DATABASE`, or similarly destructive commands unless the user explicitly requests it.
