---
name: cswap
description: Manage/switch the active Claude Code account via the cswap CLI. No arg lists accounts (active starred). Args: "bv"/"work", "personal"/"pm", "switch", "status", or email/slot. Trigger on "/cswap", "swap account", "switch claude account", "which account am I on".
---

# cswap

One Bash call per invocation; relay the output, reformatting the account list as a starred list (`★` on the active account, usage % inline).

| Arg | Command |
|-----|---------|
| *(none)* or `list` / `status` | `cswap --list` |
| `switch` / `toggle` | `cswap --switch && cswap --list` |
| `bv` / `alephvue` / `work` | `cswap --switch-to mark@alephvue.com && cswap --list` |
| `personal` / `pm` | `cswap --switch-to markrichbarbera@pm.me && cswap --list` |
| email or slot number | `cswap --switch-to <arg> && cswap --list` |

Notes:
- After a switch, remind: the new account applies to **new** `claude` sessions only; this session keeps its loaded token. Shell aliases `claude-bv` / `claude-personal` switch-and-launch.
- On switch failure (expired token, missing slot): show the error verbatim, suggest `cswap --list --token-status`. Don't retry.
- PyPI upgrade notices are informational; ignore.
