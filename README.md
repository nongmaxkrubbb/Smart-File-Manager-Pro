# Smart File Manager Pro  v4.2

A local-first desktop toolkit for Windows 10/11 (Linux/macOS also work), built with Python + PySide6.
No telemetry. No network calls except the ones you trigger in *Network Tools*.
**UI languages: English and ไทย** (Settings → Language, then restart).

## What's new in 4.3
- **SQLite storage** (`db.py`, file `~/.smart_file_manager_pro/sfm.db`, standard-library `sqlite3`, no new dependency). It replaces the JSON undo journal, the text activity log and the rules saved in QSettings:
  - `undo_batches` + `undo_moves` (1-to-many, `ON DELETE CASCADE`): undo history survives restarts; undone actions are kept as history (`status`).
  - `activity_log`: searchable (new filter box on the Activity Log page) and trimmed automatically.
  - `rules`: ordered custom organizer rules.
  - `hash_cache`: the Duplicate Finder reuses SHA-256 values while a file's size + modified time are unchanged, so repeat scans are much faster.
  - `scan_history`: every duplicate scan (files, groups, wasted bytes, cache hits/misses, seconds).
- Old `undo_journal.json`, `activity.log` and QSettings rules are imported once on first start and left untouched on disk.
- Theme, language, font, window geometry and paths still live in `QSettings`.

## What's new in 4.2
- **UI redesign** (matches the new light-violet mock-ups): slim icon sidebar with the SF logo, the seven main tools, a **⋯** tile for the other pages (Similar Photos, File Search, Processes, Network, System, Activity Log) and Settings at the bottom; no top/bottom bars.
- **Dashboard**: greeting, a *file tidiness* score ring (0-100, see `core.tidy_score`) and up to three suggestions computed from a quick read-only look at your organizer folder, plus free space / CPU / undo-count cards. The search pill opens Ctrl+K (it can also search files).
- **File Organizer** previews by itself (no Scan button): mode chips, folders-to-create bars, ticked file list, auto-organize checkbox, `Organize N files`.
- **Disk Analyzer, Duplicate Finder, Cleanup, Bulk Renamer, Settings** rebuilt as card layouts. Scan options that the design has no room for (duplicate min size; cleanup folder/size/age) are under a small **Options…** link; the 100 largest files open from the footer link.
- Settings has the theme chips, font and size, language and refresh rate again.

## What's new in 4.0
- **Cleanup** page: temp files, large files unused for N days, empty folders → Recycle Bin. Large unused files are never pre-ticked.
- **Similar Photos** page: perceptual matching (3 hashes) of burst shots / resized / re-saved copies, with thumbnails; keeps the biggest copy; move to `_Similar` (undoable) or Recycle Bin.
- **Photos by date taken** organizer mode (EXIF date, falls back to file date).
- **Crash reports**: unexpected errors are written to `~/.smart_file_manager_pro/crash.log` and a dialog shows where it is.
- **CI** (`.github/workflows/ci.yml`): tests on Linux + **Windows**, `--selftest` startup check, and a Windows `.exe` build that is smoke-tested and uploaded as an artifact.
- Sidebar scrolls when the window is short or the font is large.

## What's new in 3.2
- **UI polish**: menu icons now sit in an aligned column, no stray sort arrows until you click a header, tidier Settings buttons, Thai defaults to Leelawadee UI at 11 pt, Thai uptime units.

## What's new in 3.1
- **Font settings** (Settings → Font): choose any installed font and size (8–20 pt); headings, big numbers and the sidebar scale with it. Default stack includes Thai-capable fallbacks (Leelawadee UI, Tahoma, Noto Sans Thai).

## What's new in 3.0
- **Safety**: system folders and drive roots are refused for moves/renames/backups; scans report how many unreadable files were skipped; the **Undo history is saved to disk** and survives restarts.
- **Usability**: click any column header to sort (sizes/dates sort numerically); right-click rows → *Open file / Show in folder / Copy path* (programs and scripts are never launched from here).
- **Custom rules** in the Organizer (`*.psd` → `Design`, `invoice` → `Money`, …), saved between sessions.
- **Recycle Bin** option for duplicates (recoverable from the bin; the only action that is not undoable inside the app).
- **Auto-organize (watch folder)** with system-tray icon: new files that stopped changing for 5 s are moved using your mode and rules; every batch is undoable.
- **Thai interface**.

## Features
| Page | What it does |
|---|---|
| Dashboard | Live CPU / RAM / network sparklines, system-drive gauge, overview, quick actions, recent activity |
| File Organizer | By type, type + month, or month; custom rules; preview; untick files; auto-organize |
| Disk Analyzer | 100 largest files, space-by-type and biggest-folder charts, CSV export |
| Duplicate Finder | Size → partial hash → SHA-256; move to `_Duplicates` (oldest kept) or Recycle Bin |
| Similar Photos | Perceptual matching with thumbnails; move to `_Similar` or Recycle Bin |
| Cleanup | Temp files, big unused files, empty folders → Recycle Bin |
| File Search | Name or wildcard (`*.pdf`), minimum size, CSV export |
| Bulk Renamer | Find/replace, prefix/suffix, case, spaces, numbering; live preview; conflict detection |
| Backup | One-way copy of new/changed files with preview; never deletes |
| Processes | Read-only top processes |
| Network Tools | Interfaces, ping + DNS, single-port TCP check |
| System Info / Activity Log / Settings | language, refresh rate |

Shortcuts: **Ctrl+K** quick switcher, **Ctrl+1…9** pages. Drag a folder onto any path box.

## Run
```powershell
.\run.bat
```
or:
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

## Build a standalone .exe
```powershell
.\build_exe.bat
```
Output: `dist\SmartFileManagerPro.exe` (unsigned, so Windows SmartScreen may warn).

## Tests
```bash
python -m unittest discover -s tests -v     # core + headless UI + i18n coverage (43 tests)
```

## Layout
```
app.py       UI                    core.py      all file/network logic (Qt-free, unit-tested)
widgets.py   charts, toast, items  themes.py    color themes + stylesheet
crash.py     crash-report handler  .github/     CI workflow (Linux + Windows, exe build)
i18n.py / i18n_th.py  translation  tests/       core, UI and i18n tests
```

## Safety
- Nothing moves until you confirm; the only deleting action is the optional *Recycle checked…* button (to the OS Recycle Bin).
- Existing files are never overwritten (`file (1).pdf` is used on conflict).
- Unfinished downloads, hidden files and symlinks are skipped.
- Undo covers the last 20 move/rename batches, including after a restart (entries whose files are gone are dropped).
- Auto-organize is off by default and asks for confirmation when enabled.

## Known limits
- Developed and tested headless on Linux. The included GitHub Actions workflow runs the same tests and a startup check on Windows — push to GitHub to get that verification.
- Not handled yet: Windows paths over 260 characters, files locked by other programs (they are reported as errors, not forced).
- Activity-log lines and OS-provided ping output stay in English.
- Undo does not cover Recycle Bin actions or Backup copies.
