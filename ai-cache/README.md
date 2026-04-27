# AI Tool Cache

This directory is the designated stash location for AI-assistant and workflow tool caches
so the project root stays clean.

## Contents

### `cursor/`
Snapshot of this project's Cursor IDE cache, copied from
`C:\Users\RTX\.cursor\projects\c-Users-RTX-Desktop-StreamingCommunity-main\`.

Subfolders:
- `agent-transcripts/` — Full JSONL transcripts of past chats with the Cursor agent.
- `canvases/` — Cursor canvas scratchpads (may be empty).
- `mcps/` — Installed MCP server descriptors (user-browser-tools, user-chrome-devtools, cursor-ide-browser).
- `terminals/` — Snapshots of recent terminal output as captured by Cursor.

Note: this is a point-in-time copy. The live Cursor cache continues to live at the
`%USERPROFILE%\.cursor\projects\...` path and is what the IDE actively reads/writes.
Re-snapshot by re-copying that folder into here whenever you want an updated backup.

## History

The previous `.zencoder/` and `.zenflow/` folders at the repo root were removed earlier
and could not be restored (PowerShell `Remove-Item` bypasses the Windows Recycle Bin).
Those tools will recreate their caches automatically on next use. Move any new tool-created
cache folders in here to keep the root clean.
