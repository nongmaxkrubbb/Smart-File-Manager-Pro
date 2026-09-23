"""Pure-Python logic (no Qt) so everything here can be unit-tested.

Long-running functions accept `cancel` (callable -> bool) and `progress` (callable(str)).
Nothing here ever deletes a user file.
"""
import os
import re
import csv
import json
import heapq
import shutil
import socket
import string
import hashlib
import fnmatch
import itertools
import platform
import subprocess
import time
from pathlib import Path
from datetime import datetime

SYSTEM_DIRS = {"System Volume Information", "$Recycle.Bin", "$RECYCLE.BIN"}
SKIP_DIRS = SYSTEM_DIRS | {"_Duplicates", "_Similar"}
SKIP_FILES = {"desktop.ini", "thumbs.db"}
PARTIAL_EXTS = {"crdownload", "part", "tmp", "download", "partial"}
CATEGORY_MAP = {
    "Images": {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "heic", "tiff", "ico"},
    "Documents": {"pdf", "doc", "docx", "txt", "rtf", "odt", "xls", "xlsx", "ppt", "pptx", "csv", "md"},
    "Archives": {"zip", "7z", "rar", "tar", "gz", "bz2", "xz"},
    "Installers": {"exe", "msi", "dmg", "pkg", "deb", "apk"},
    "Audio": {"mp3", "wav", "flac", "m4a", "ogg", "aac"},
    "Videos": {"mp4", "mkv", "mov", "avi", "webm", "wmv"},
    "Code": {"py", "js", "ts", "html", "css", "json", "lua", "c", "cpp", "java", "cs", "go", "rs", "sh", "bat"},
}
CATEGORY_DIRS = set(CATEGORY_MAP) | {"Others", "_Duplicates", "_Similar"}
EXECUTABLE_EXTS = {"exe", "bat", "cmd", "com", "msi", "ps1", "scr", "vbs", "js", "jar", "lnk", "reg"}
EXTRA_DIRS = set()          # folders created by custom rules (so Undo can tidy them)
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
ORGANIZE_MODES = ("By type", "By type + month", "By month", "Photos by date taken")
IMAGE_EXTS = {"jpg", "jpeg", "png", "bmp", "webp", "tif", "tiff"}
EXIF_EXTS = {"jpg", "jpeg", "tif", "tiff", "webp"}
TEMP_PATTERNS = (("*.tmp", 1), ("*.temp", 1), ("~$*", 1), ("thumbs.db", 1), (".ds_store", 1), ("*.dmp", 1),
                 ("*.chk", 1), ("*.crdownload", 7), ("*.part", 7))
HOST_RE = re.compile(r"^[A-Za-z0-9._:\-]+$")
ILLEGAL_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def never():
    return False


def noop(_msg=""):
    return None


def fmt_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_duration(seconds, units=("d", "h", "m")):
    seconds = int(seconds)
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    ud, uh, um = units
    return (f"{d}{ud} " if d else "") + f"{h}{uh} {m}{um}"


def categorize(path):
    ext = os.path.splitext(str(path))[1].lower().lstrip(".")
    for cat, exts in CATEGORY_MAP.items():
        if ext in exts:
            return cat
    return "Others"


def unique_dest(dest_dir, name):
    """A path in dest_dir that does not exist yet (adds ' (n)' when needed)."""
    dest = Path(dest_dir) / name
    if not dest.exists():
        return dest
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 1
    while True:
        cand = Path(dest_dir) / f"{stem} ({n}){suffix}"
        if not cand.exists():
            return cand
        n += 1


def protected_reason(path):
    """Why moving/renaming files in `path` is refused (system folder / drive root), or None."""
    try:
        p = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        p = Path(path)
    if p.parent == p:
        return "a drive or filesystem root"
    if platform.system().lower() == "windows":
        cands = [os.environ.get(k) for k in ("WINDIR", "SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")]
    else:
        cands = ["/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sbin", "/sys", "/usr",
                 "/System", "/Library", "/Applications"]
    for c in filter(None, cands):
        try:
            c = Path(c).resolve()
        except (OSError, RuntimeError):
            c = Path(c)
        if p == c or c in p.parents:
            return f"a system folder ({c})"
    return None


def walk_files(root, cancel=never, skip_dirs=SKIP_DIRS, errors=None):
    """Yield (path, stat) for regular files; skips symlinks and protected folders.
    If `errors` is a list, unreadable files/folders are recorded in it instead of vanishing silently."""
    onerr = (lambda e: errors.append(str(e))) if errors is not None else (lambda e: None)
    for current, dirs, names in os.walk(str(root), onerror=onerr):
        if cancel():
            return
        dirs[:] = [d for d in dirs if d not in skip_dirs and not os.path.islink(os.path.join(current, d))]
        for name in names:
            if cancel():
                return
            p = os.path.join(current, name)
            try:
                if os.path.islink(p):
                    continue
                yield p, os.stat(p)
            except OSError as e:
                if errors is not None:
                    errors.append(str(e))
                continue


# ---------------------------------------------------------------- organizer
def parse_rules(raw):
    """[{'pattern','folder'}] -> validated [(pattern_lower, folder)]. Invalid entries are dropped."""
    rules = []
    for r in raw or []:
        pat = str(r.get("pattern", "")).strip().lower()
        folder = str(r.get("folder", "")).strip()
        if not pat or not folder or folder in (".", "..") or ILLEGAL_NAME.search(folder) or folder.endswith((" ", ".")):
            continue
        rules.append((pat, folder))
    return rules


def set_extra_dirs(rules):
    EXTRA_DIRS.clear()
    EXTRA_DIRS.update(f for _p, f in rules)


def rule_folder(name, rules):
    n = name.lower()
    for pat, folder in rules or []:
        if any(ch in pat for ch in "*?["):
            if fnmatch.fnmatch(n, pat):
                return folder
        elif pat in n:
            return folder
    return None


def exif_date(path):
    """Date a photo was taken (EXIF DateTimeOriginal, else DateTime), or None."""
    if os.path.splitext(str(path))[1].lower().lstrip(".") not in EXIF_EXTS:
        return None
    try:
        from PIL import Image
        with Image.open(path) as im:
            ex = im.getexif()
            raw = ex.get_ifd(0x8769).get(0x9003) or ex.get(0x9003) or ex.get(0x0132)
        if raw:
            return datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


def plan_organize(folder, mode="By type", rules=None):
    """-> [(src Path, category, size, dest_dir Path)] for top-level files of folder."""
    folder = Path(folder)
    plan = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
        try:
            if p.is_symlink() or not p.is_file():
                continue
            if p.name.startswith(".") or p.name.lower() in SKIP_FILES:
                continue
            if p.suffix.lower().lstrip(".") in PARTIAL_EXTS:
                continue
            st = p.stat()
        except OSError:
            continue
        cat = rule_folder(p.name, rules) or categorize(p)
        month = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m")
        if mode == "Photos by date taken" and cat == "Images":
            taken = exif_date(p)
            dest = folder / "Images" / (taken.strftime("%Y-%m") if taken else month)
        elif mode == "By month":
            dest = folder / month
        elif mode == "By type + month":
            dest = folder / cat / month
        else:
            dest = folder / cat
        plan.append((p, cat, st.st_size, dest))
    return plan


def move_files(pairs, cancel=never, progress=noop):
    """pairs: [(src, dest_dir)] -> (moves [(src, dest)], skipped, errors [str]). Never overwrites."""
    moves, skipped, errors = [], 0, []
    total, prot = len(pairs), {}
    for i, (src, dest_dir) in enumerate(pairs, 1):
        if cancel():
            break
        if i % 10 == 0 or i == total:
            progress(f"Moving {i}/{total}…")
        src, dest_dir = Path(src), Path(dest_dir)
        reason = None
        for d in (src.parent, dest_dir):
            if d not in prot:
                prot[d] = protected_reason(d)
            reason = reason or prot[d]
        if reason:
            errors.append(f"{src.name}: refused, {reason}")
            continue
        if not src.exists():
            skipped += 1
            continue
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = unique_dest(dest_dir, src.name)
            shutil.move(str(src), str(dest))
            moves.append((src, dest))
        except Exception as e:
            errors.append(f"{src.name}: {e}")
    return moves, skipped, errors


def _cleanup_empty(d):
    d = Path(d)
    while d.name in CATEGORY_DIRS or d.name in EXTRA_DIRS or MONTH_RE.match(d.name):
        try:
            d.rmdir()          # only succeeds when empty
        except OSError:
            break
        d = d.parent


def undo_moves(moves, cancel=never, progress=noop):
    """Revert [(src, dest)] -> (restored, skipped, errors [str])."""
    restored, skipped, errors = 0, 0, []
    total = len(moves)
    for i, (src, dest) in enumerate(reversed(moves), 1):
        if i % 10 == 0 or i == total:
            progress(f"Restoring {i}/{total}…")
        src, dest = Path(src), Path(dest)
        if not dest.exists() or src.exists():
            skipped += 1
            continue
        try:
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(src))
            restored += 1
            _cleanup_empty(dest.parent)
        except Exception as e:
            errors.append(f"{dest.name}: {e}")
    return restored, skipped, errors


def save_journal(path, stack):
    """Persist the undo stack so actions can be reverted after a restart."""
    data = [{"label": label, "moves": [[str(s), str(d)] for s, d in moves]} for label, moves in stack[-20:]]
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def load_journal(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for e in data[-20:] if isinstance(data, list) else []:
        try:
            moves = [(Path(s), Path(d)) for s, d in e.get("moves", []) if Path(d).exists()]
        except (TypeError, ValueError):
            continue
        if moves:
            out.append((str(e.get("label", "Earlier action")), moves))
    return out


def trash_files(paths, cancel=never, progress=noop):
    """Send files to the OS Recycle Bin / Trash (recoverable there). -> (trashed, errors)."""
    try:
        from send2trash import send2trash
    except ImportError:
        raise RuntimeError("Recycle Bin support needs the 'send2trash' package (pip install send2trash).")
    done, errors, prot = [], [], {}
    for i, p in enumerate(paths, 1):
        if cancel():
            break
        parent = Path(p).parent
        if parent not in prot:
            prot[parent] = protected_reason(parent)
        if prot[parent]:
            errors.append(f"{os.path.basename(p)}: refused, {prot[parent]}")
            continue
        try:
            send2trash(str(p))
            done.append(p)
        except Exception as e:
            errors.append(f"{os.path.basename(p)}: {e}")
        if i % 10 == 0 or i == len(paths):
            progress(f"Recycling {i}/{len(paths)}…")
    return done, errors


# ------------------------------------------------------------- disk analyzer
def analyze_disk(root, limit=100, cancel=never, progress=noop):
    root = os.path.normpath(str(root))
    heap, counter = [], itertools.count()
    cats, folders, scanned, total, errs = {}, {}, 0, 0, []
    for p, st in walk_files(root, cancel, errors=errs):
        scanned += 1
        total += st.st_size
        cat = categorize(p)
        cats[cat] = cats.get(cat, 0) + st.st_size
        rel = p[len(root):].lstrip(os.sep)
        key = rel.split(os.sep, 1)[0] if os.sep in rel else "(files in folder)"
        folders[key] = folders.get(key, 0) + st.st_size
        item = (st.st_size, next(counter), p, st.st_mtime)
        if len(heap) < limit:
            heapq.heappush(heap, item)
        elif item[0] > heap[0][0]:
            heapq.heapreplace(heap, item)
        if scanned % 500 == 0:
            progress(f"Scanning… {scanned} files")
    top = [(s, p, m) for s, _, p, m in sorted(heap, reverse=True)]
    return {"top": top, "categories": cats, "folders": folders,
            "scanned": scanned, "total": total, "cancelled": cancel(), "unreadable": len(errs)}


# ----------------------------------------------------------------- duplicates
def _hash_file(path, cancel, limit=None):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            if limit:
                h.update(f.read(limit))
            else:
                while True:
                    if cancel():
                        return None
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _cached_hash(path, size, mtime, kind, cancel, cache, limit=None):
    """SHA-256 of a file (or of its first `limit` bytes), reusing a stored value while size + mtime are unchanged."""
    if cache is not None:
        h = cache.get(path, size, mtime, kind)
        if h:
            return h
    h = _hash_file(path, cancel, limit=limit)
    if h and cache is not None:
        cache.put(path, size, mtime, kind, h)
    return h


def find_duplicates(root, min_size=1, cancel=never, progress=noop, cache=None):
    """cache: optional object with get/put/flush (see db.HashCache) so unchanged files are not re-hashed."""
    by_size, scanned, errs = {}, 0, []
    started = time.monotonic()
    for p, st in walk_files(root, cancel, errors=errs):
        if st.st_size < max(min_size, 1):
            continue
        by_size.setdefault(st.st_size, []).append((p, st.st_mtime))
        scanned += 1
        if scanned % 500 == 0:
            progress(f"Indexing files… {scanned}")
    groups = []
    candidates = [(s, fl) for s, fl in by_size.items() if len(fl) >= 2]
    try:
        for i, (size, files) in enumerate(candidates, 1):
            if cancel():
                break
            if i % 20 == 0 or i == len(candidates):
                progress(f"Comparing candidates {i}/{len(candidates)}…")
            by_head = {}
            for p, m in files:
                h = _cached_hash(p, size, m, "head", cancel, cache, limit=65536)
                if h:
                    by_head.setdefault(h, []).append((p, m))
            for head_files in by_head.values():
                if len(head_files) < 2:
                    continue
                by_full = {}
                for p, m in head_files:
                    h = _cached_hash(p, size, m, "full", cancel, cache)
                    if h:
                        by_full.setdefault(h, []).append((p, m))
                for g in by_full.values():
                    if len(g) >= 2:
                        g.sort(key=lambda x: x[1])      # oldest first = the copy to keep
                        groups.append((size, g))
    finally:
        if cache is not None:
            cache.flush()
    groups.sort(key=lambda x: x[0] * (len(x[1]) - 1), reverse=True)
    return {"groups": groups, "scanned": scanned, "cancelled": cancel(), "unreadable": len(errs),
            "seconds": time.monotonic() - started}


# -------------------------------------------------------------------- cleanup
def find_cleanup(root, min_mb=100, old_days=180, cancel=never, progress=noop):
    """Candidates to clean: temp files, large files unused for a long time, empty folders.
    -> {'items': [(kind, path, size, mtime)], ...}. Nothing is changed here."""
    now, items, scanned, errs = time.time(), [], 0, []
    for p, st in walk_files(root, cancel, errors=errs):
        scanned += 1
        name = os.path.basename(p).lower()
        age = (now - max(st.st_atime, st.st_mtime)) / 86400
        if any(fnmatch.fnmatch(name, pat) and age >= min_age for pat, min_age in TEMP_PATTERNS):
            items.append(("temp", p, st.st_size, st.st_mtime))
        elif st.st_size >= min_mb * 1024 * 1024 and age >= old_days:
            items.append(("old_large", p, st.st_size, st.st_mtime))
        if scanned % 1000 == 0:
            progress(f"Scanning… {scanned} files")
    nonempty, empty = set(), set()

    def on_err(e):
        if getattr(e, "filename", None):
            nonempty.add(str(e.filename))          # unreadable folder: assume it has content
        errs.append(str(e))
    root = os.path.normpath(str(root))
    for current, dirs, files in os.walk(root, topdown=False, onerror=on_err):
        if cancel():
            break
        kids_busy = any(os.path.join(current, d) in nonempty or os.path.islink(os.path.join(current, d)) for d in dirs)
        if files or kids_busy or os.path.basename(current) in SYSTEM_DIRS:
            nonempty.add(current)
        elif current != root:
            empty.add(current)
    for d in sorted(empty):
        if os.path.dirname(d) not in empty:         # report only the top-most empty folder
            try:
                items.append(("empty_dir", d, 0, os.stat(d).st_mtime))
            except OSError:
                pass
    return {"items": items, "scanned": scanned, "cancelled": cancel(), "unreadable": len(errs)}


# ---------------------------------------------------------------- similar photos
def _fingerprint(path):
    """-> ((h_dhash, v_dhash, ahash, mean_brightness), (width, height)).
    Three hashes together avoid false matches (e.g. a flat dark image vs. a smooth gradient)."""
    from PIL import Image
    with Image.open(path) as im:
        size = im.size
        try:
            im.draft("L", (128, 128))             # fast JPEG decode at reduced size
        except Exception:
            pass
        g = im.convert("L").resize((9, 9), Image.LANCZOS)
    px = g.tobytes()                               # 9x9 bytes, row-major
    hh = hv = ah = 0
    block = [px[y * 9 + x] for y in range(8) for x in range(8)]
    mean = sum(block) / 64
    for y in range(8):
        for x in range(8):
            hh = (hh << 1) | (1 if px[y * 9 + x] > px[y * 9 + x + 1] else 0)
            hv = (hv << 1) | (1 if px[y * 9 + x] > px[(y + 1) * 9 + x] else 0)
            ah = (ah << 1) | (1 if px[y * 9 + x] > mean else 0)
    return (hh, hv, ah, mean), size


def _close(a, b, max_distance):
    pop = lambda v: bin(v).count("1")           # noqa: E731
    return (pop(a[0] ^ b[0]) <= max_distance and pop(a[1] ^ b[1]) <= max_distance
            and pop(a[2] ^ b[2]) <= max_distance and abs(a[3] - b[3]) <= 40)


def find_similar_images(root, max_distance=6, cancel=never, progress=noop):
    """Group visually similar photos (perceptual hashes). The best copy (largest) is listed first."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        raise RuntimeError("Similar-photo search needs the 'Pillow' package (pip install Pillow).")
    hashes, errs, scanned = [], [], 0
    for p, st in walk_files(root, cancel, errors=errs):
        if os.path.splitext(p)[1].lower().lstrip(".") not in IMAGE_EXTS or st.st_size > 60 * 1024 * 1024:
            continue
        try:
            fp, size = _fingerprint(p)
        except Exception as e:
            errs.append(str(e))
            continue
        hashes.append((fp, p, st.st_size, st.st_mtime, size))
        scanned += 1
        if scanned % 50 == 0:
            progress(f"Reading photos… {scanned}")
    n = len(hashes)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    parts = max_distance + 1                         # pigeonhole: a close pair shares at least one identical chunk
    width = 64 // parts + 1
    buckets = {}
    for idx, (fp, *_r) in enumerate(hashes):
        for k in range(parts):
            buckets.setdefault((k, (fp[0] >> (k * width)) & ((1 << width) - 1)), []).append(idx)
    for members in buckets.values():
        if cancel():
            break
        if len(members) < 2 or len(members) > 400:   # skip degenerate buckets (flat/blank images)
            continue
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                i, j = members[a], members[b]
                if _close(hashes[i][0], hashes[j][0], max_distance):
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[ri] = rj
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        files = [(hashes[i][1], hashes[i][2], hashes[i][3], hashes[i][4]) for i in members]
        files.sort(key=lambda f: (f[3][0] * f[3][1], f[1]), reverse=True)   # best = most pixels, then biggest file
        out.append(files)
    out.sort(key=lambda g: sum(f[1] for f in g[1:]), reverse=True)
    return {"groups": out, "scanned": n, "cancelled": cancel(), "unreadable": len(errs)}


# --------------------------------------------------------------------- search
def search_files(root, query="", min_size=0, max_results=2000, cancel=never, progress=noop):
    q = query.strip().lower()
    wildcard = any(ch in q for ch in "*?[")
    results, scanned, errs = [], 0, []
    for p, st in walk_files(root, cancel, errors=errs):
        scanned += 1
        if st.st_size < min_size:
            continue
        name = os.path.basename(p).lower()
        ok = (not q) or (fnmatch.fnmatch(name, q) if wildcard else q in name)
        if ok:
            results.append((p, st.st_size, st.st_mtime))
            if len(results) >= max_results:
                break
        if scanned % 1000 == 0:
            progress(f"Searching… {scanned} files checked, {len(results)} found")
    return {"results": results, "scanned": scanned, "truncated": len(results) >= max_results,
            "cancelled": cancel(), "unreadable": len(errs)}


# -------------------------------------------------------------------- renamer
def plan_rename(files, find="", replace="", prefix="", suffix="", case="Keep",
                spaces="Keep", number=False, start=1, pad=3):
    """-> [(Path, new_name, status)] with status OK / Unchanged / Invalid / Conflict."""
    out = []
    for i, f in enumerate(files):
        p = Path(f)
        s = p.stem
        if find:
            s = s.replace(find, replace)
        if spaces == "Underscore":
            s = re.sub(r"\s+", "_", s)
        elif spaces == "Dash":
            s = re.sub(r"\s+", "-", s)
        elif spaces == "Remove":
            s = re.sub(r"\s+", "", s)
        if case == "lowercase":
            s = s.lower()
        elif case == "UPPERCASE":
            s = s.upper()
        elif case == "Title Case":
            s = string.capwords(s)
        s = f"{prefix}{s}{suffix}"
        if number:
            s = f"{s}_{start + i:0{pad}d}"
        new = s + p.suffix
        if new == p.name:
            status = "Unchanged"
        elif not s.strip() or ILLEGAL_NAME.search(new) or new.endswith((" ", ".")):
            status = "Invalid"
        else:
            status = "OK"
        out.append([p, new, status])
    prot = {}
    for item in out:
        parent = item[0].parent
        if parent not in prot:
            prot[parent] = protected_reason(parent)
        if prot[parent] and item[2] == "OK":
            item[2] = "Protected"
    new_keys = {}
    for p, new, _ in out:
        new_keys.setdefault((str(p.parent), new.lower()), []).append(p)
    old_keys = {(str(p.parent), p.name.lower()) for p, _, _ in out}
    for item in out:
        if item[2] != "OK":
            continue
        p, new = item[0], item[1]
        key = (str(p.parent), new.lower())
        own = (str(p.parent), p.name.lower())
        if len(new_keys[key]) > 1:
            item[2] = "Conflict"
        elif key != own and key in old_keys:
            item[2] = "Conflict"
        elif key != own and (p.parent / new).exists():
            item[2] = "Conflict"
    return [tuple(x) for x in out]


def apply_rename(plan, cancel=never, progress=noop):
    moves, errors = [], []
    for i, (p, new, status) in enumerate(plan, 1):
        if status != "OK":
            continue
        if cancel():
            break
        if i % 10 == 0:
            progress(f"Renaming {i}/{len(plan)}…")
        dest = p.with_name(new)
        try:
            p.rename(dest)
            moves.append((p, dest))
        except OSError as e:
            errors.append(f"{p.name}: {e}")
    return moves, errors


# --------------------------------------------------------------------- backup
def plan_backup(src, dst, cancel=never, progress=noop):
    src, dst = Path(src).resolve(), Path(dst).resolve()
    if not src.is_dir():
        raise ValueError("Source folder does not exist.")
    if src == dst or src in dst.parents:
        raise ValueError("The destination must not be the source folder or inside it.")
    reason = protected_reason(dst)
    if reason:
        raise ValueError(f"Refusing to write a backup into {reason}.")
    plan, scanned, errs = [], 0, []
    for p, st in walk_files(src, cancel, skip_dirs=SYSTEM_DIRS, errors=errs):
        target = dst / os.path.relpath(p, src)
        reason = None
        try:
            if not target.exists():
                reason = "new"
            else:
                ts = target.stat()
                if ts.st_size != st.st_size or st.st_mtime > ts.st_mtime + 2:
                    reason = "changed"
        except OSError:
            reason = "new"
        if reason:
            plan.append((p, str(target), st.st_size, reason))
        scanned += 1
        if scanned % 500 == 0:
            progress(f"Comparing… {scanned} files")
    return {"plan": plan, "scanned": scanned, "bytes": sum(x[2] for x in plan), "cancelled": cancel(),
            "unreadable": len(errs)}


def run_backup(plan, cancel=never, progress=noop):
    """Copies new/changed files only. Never deletes anything in source or destination."""
    copied, nbytes, errors = 0, 0, []
    for i, (s, t, size, _reason) in enumerate(plan, 1):
        if cancel():
            break
        try:
            Path(t).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, t)
            copied += 1
            nbytes += size
        except OSError as e:
            errors.append(f"{os.path.basename(s)}: {e}")
        if i % 20 == 0 or i == len(plan):
            progress(f"Copying {i}/{len(plan)}…")
    return {"copied": copied, "bytes": nbytes, "errors": errors, "cancelled": cancel()}


# -------------------------------------------------------------------- network
def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("10.255.255.255", 1))     # UDP connect sends no packet
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def valid_host(host):
    return bool(host) and not host.startswith("-") and bool(HOST_RE.match(host))


def ping_host(host, cancel=never, progress=noop):
    if not valid_host(host):
        return "Invalid host name."
    try:
        start = time.perf_counter()
        infos = socket.getaddrinfo(host, None)
        dns_ms = (time.perf_counter() - start) * 1000
        addrs = sorted({i[4][0] for i in infos})
        if platform.system().lower() == "windows":
            cmd = ["ping", "-n", "2", "-w", "3000", host]
        else:
            cmd = ["ping", "-c", "2", "-W", "3", host]
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=12,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (proc.stdout or proc.stderr or "").strip()
        head = f"{host} → {', '.join(addrs[:4])}\nDNS resolution: {dns_ms:.1f} ms\n"
        if proc.returncode == 0:
            return f"Reachable.\n{head}\n{out}"
        return f"DNS resolved, but ping got no reply.\n{head}\n{out}"
    except socket.gaierror as e:
        return f"DNS lookup failed for {host}: {e}"
    except subprocess.TimeoutExpired:
        return f"Ping to {host} timed out."
    except FileNotFoundError:
        return "The 'ping' command is not available on this system."
    except Exception as e:
        return f"Network check failed: {e}"


def check_port(host, port, cancel=never, progress=noop):
    if not valid_host(host):
        return "Invalid host name."
    try:
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        return "Port must be a number between 1 and 65535."
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=3):
            ms = (time.perf_counter() - start) * 1000
            return f"{host}:{port} is OPEN (connected in {ms:.0f} ms)"
    except socket.gaierror as e:
        return f"DNS lookup failed for {host}: {e}"
    except (socket.timeout, TimeoutError):
        return f"{host}:{port} timed out (filtered or unreachable)"
    except OSError as e:
        return f"{host}:{port} is CLOSED or unreachable ({e})"


def export_csv(path, headers, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig: opens cleanly in Excel
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


# ------------------------------------------------------------------ dashboard
def tidy_score(loose=0, dup_files=0, dup_bytes=0, temp_bytes=0, old_large_bytes=0, empty_dirs=0):
    """0-100 "how tidy are your files". Starts at 100 and loses points for clutter (each part is capped):
    loose files in the folder (max -35), duplicate copies (max -25), temp files (max -20),
    big files nobody opened for ages (max -10), empty folders (max -10)."""
    mb, gb = 1024 ** 2, 1024 ** 3
    s = 100.0
    s -= min(35.0, loose * 0.25)
    s -= min(25.0, dup_files * 2.5 + dup_bytes / gb * 5)
    s -= min(20.0, temp_bytes / (50 * mb))
    s -= min(10.0, old_large_bytes / gb * 2)
    s -= min(10.0, empty_dirs * 2.0)
    return max(0, min(100, round(s)))


def health_summary(folder, budget_s=8.0, min_dup_size=100 * 1024, cancel=never, progress=noop):
    """Quick read-only look at one folder for the dashboard: loose files, duplicate copies, temp files...
    Gives up after budget_s seconds (partial=True) so a huge folder never keeps the app busy."""
    end = time.monotonic() + budget_s
    stop = lambda: cancel() or time.monotonic() > end                         # noqa: E731
    loose = plan_organize(folder)
    kinds = {c for _p, c, _s, _d in loose}
    dups = find_duplicates(folder, min_dup_size, stop)
    clean = find_cleanup(folder, 100, 180, stop)
    dup_files = sum(len(g) - 1 for _s, g in dups["groups"])
    dup_bytes = sum(s * (len(g) - 1) for s, g in dups["groups"])
    temp = [it for it in clean["items"] if it[0] == "temp"]
    old_large = [it for it in clean["items"] if it[0] == "old_large"]
    empty = [it for it in clean["items"] if it[0] == "empty_dir"]
    out = {"folder": str(folder), "loose": len(loose), "kinds": len(kinds),
           "dup_groups": len(dups["groups"]), "dup_files": dup_files, "dup_bytes": dup_bytes,
           "temp_count": len(temp), "temp_bytes": sum(it[2] for it in temp),
           "old_large_bytes": sum(it[2] for it in old_large), "empty_dirs": len(empty),
           "partial": time.monotonic() > end or cancel()}
    out["score"] = tidy_score(out["loose"], dup_files, dup_bytes, out["temp_bytes"], out["old_large_bytes"], out["empty_dirs"])
    return out
