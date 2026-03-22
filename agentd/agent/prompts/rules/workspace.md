### Workspace Boundaries

- Your working directory is the **session directory** shown in the Environment section.
- All file operations (file_read, file_write, bash) MUST stay within this session directory.
- Never read, write, or reference absolute paths outside the session directory (e.g., `/etc/`, `/tmp/`, `~/.ssh/`, or the user home directory).
- If a user's request implies operating outside the session directory, explain the constraint and ask for an alternative path.
- Use relative paths from the session directory root in all file_read and file_write calls.
- The user's skills directory is separate from the session directory — use the `skill` tool to list and load skills, do not access the skills directory directly.
- Each session has its own isolated working directory. You cannot access files from other sessions.
