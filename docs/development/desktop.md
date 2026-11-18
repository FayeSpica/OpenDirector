# Desktop application

[中文](desktop.zh-CN.md)

The desktop application reuses the Web UI and Python services. Electron owns one Python Host, started on an OS-assigned loopback port through a private JSON-lines control channel. Each launch has a fresh credential; only the main process forwards authenticated requests. The renderer uses `opendirector://app`, has no Node access, and has a narrow bridge for registering its edit-session identity and reporting language and export activity.

Application resources are read-only. Electron user data contains `workspace/` (projects and configuration), `logs/` and a locale cache. Existing source or pip workspaces are not moved or overwritten. File inputs use native dialogs; downloads use the native save dialog. Closing the window hides it and keeps exports alive. Quit checks local Agent, generation, tool and renderer export activity, asks before interruption, then stops the Host and releases only edit sessions registered by its own window. The same cleanup runs when the parent control pipe closes. Remote jobs may continue and incur charges; shutdown does not claim remote cancellation.

## Build and check

Build on the target OS and architecture. Build prerequisites are Node.js from `.nvmrc`, npm, uv, and standalone FFmpeg/ffprobe binaries. End users do not need those development tools.

```bash
npm ci --prefix apps/desktop
npm test --prefix apps/desktop
OPENDIRECTOR_FFMPEG_DIR=/absolute/path/to/standalone-media npm run prepare:runtime --prefix apps/desktop
npm run pack --prefix apps/desktop
node apps/desktop/scripts/smoke.mjs /absolute/path/to/packaged/OpenDirector
```

The runtime preparation builds the Python wheel using the existing build backend, including Web assets, skills and the actual OpenCode executable. It installs the wheel and dependencies into a private standalone Python distribution, validates CLI help and both media binaries, and records the target in `runtime/manifest.json`. It never modifies system Python. Supply media binaries with their redistribution licenses for the target; system package-manager binaries with external shared-library dependencies are not portable.

The smoke check launches the actual packaged executable with isolated temporary data, opens the canvas, checks authenticated health and project queries, verifies renderer isolation and both locales, hides the window without stopping the Host, and reopens the same project after quitting. It makes no provider calls. It does not establish Agent-assisted creation, export quality, signing, or support on an untested platform.

## Terminal command

Use **Manage terminal command** in the application menu to install the launcher in `~/.local/bin`. Add that directory to PATH if needed. The app preserves existing entries. To uninstall or repair an old installation, check the path shown in the app and manually remove the OpenDirector entry. To repair it, run Install again. `opendirector desktop` opens this installation; `opendirector serve` runs the ordinary standalone API. Business CLI commands retain their existing `--url`, project, session and revision rules. Automatic attachment of an external CLI to the private desktop Host is not available.

## Delivery boundary

This is the shared application for #363. Platform installers, signing and distribution are #364–#366; automatic updates are #367. Browser rendering still requires the application process. Closing the application cannot finish a browser export in the background. Microphone access is not enabled in this initial shell.

Before release, run the complete clean-machine acceptance in #363 on each declared platform: Agent-assisted creation and tool use, editing, export, reopen, failure recovery, duplicate launch and locks. A successful smoke check alone is insufficient. OS/architecture support and signing status must be recorded with each platform's acceptance evidence.

## Release packages

The desktop release workflow builds an unsigned Apple Silicon DMG and an unsigned Windows x64 NSIS installer. It checks both packages on their native runners before attaching them and their SHA-256 files to the same draft release. Windows changes also run the installer smoke check on pull requests that touch desktop packaging.
