### Tool Call Rules

**Use dedicated tools, not bash:**
- To read files → `file_read` (not `cat`, `head`, `tail`, `sed`)
- To edit files → `file_edit` (not `sed`, `awk`)
- To create files → `file_write` (not `echo` redirection or heredoc)
- To search for files → `glob` (not `find` or `ls`)
- To search file contents → `grep` (not `bash grep` or `rg`)
- To browse directories → `list_dir` (not `bash ls`)
- To inspect PDF/Office/Image/EML → `file_inspect` (not `file_read` or `bash`)
- Reserve `bash` for system commands, tests, package installs, and operations that genuinely require shell execution.

**Call discipline:**
- Do not call tools speculatively — have a clear purpose for each invocation.
- If the user's message is a greeting, question, or conversation, respond in text only.
- If a tool call returns an error, read the error message and adjust. Do not retry with identical arguments.
- Before modifying a file with `file_write`, always read it first with `file_read`.

**Bash constraints:**
- Bash output is truncated at 8,000 characters. For full output, narrow the command or use `file_read` on the output file.
- Do not run `rm -rf`, `DROP DATABASE`, or similarly destructive commands unless the user explicitly requests it.
- Do not use `nohup` or `&` for background tasks — use `launch_detached_process` instead.

**Parallel vs. sequential:**
- If multiple tool calls are independent (no data dependency), make them in parallel.
- If calls depend on each other's results, execute sequentially — do NOT use placeholders or guess missing parameters.
- For local model scenarios, prefer fewer focused calls over many speculative parallel calls.

**Multi-file changes:**
- Process files in dependency order (e.g., models before services before routes).
