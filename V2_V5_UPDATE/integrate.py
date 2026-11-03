#!/usr/bin/env python
"""Install the V2 + V5 update onto your TransformoDocs V1 project.

Run it from anywhere:
    python V2_V5_UPDATE/integrate.py              (the folder sits inside your V1 project)
    python V2_V5_UPDATE/integrate.py C:\\path\\to\\transformodocs
Options:
    --dry-run   show what would change, change nothing
    --undo      restore the V1 files from the backup and remove the files this update added
Files that already exist are backed up to _backup_before_v2_v5/ before being replaced.
"""
import argparse
import filecmp
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAYLOAD = HERE / "payload"
BACKUP_NAME = "_backup_before_v2_v5"


def find_project(arg):
    candidates = [Path(arg).resolve()] if arg else [HERE.parent, Path.cwd()]
    for c in candidates:
        if (c / "manage.py").exists() and (c / "core" / "services" / "pipeline.py").exists():
            return c
    sys.exit("ERROR: could not find your TransformoDocs V1 project.\n"
             "Put the V2_V5_UPDATE folder inside the project (next to manage.py) and run again,\n"
             "or pass the project folder:  python V2_V5_UPDATE/integrate.py <path-to-project>")


def payload_files():
    return sorted(p.relative_to(PAYLOAD) for p in PAYLOAD.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def install(project, dry):
    backup = project / BACKUP_NAME
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"replaced": [], "added": []}
    added, replaced, same = [], [], 0
    for rel in payload_files():
        src, dst = PAYLOAD / rel, project / rel
        if dst.exists():
            if filecmp.cmp(src, dst, shallow=False):
                same += 1
                continue
            replaced.append(str(rel))
            bak = backup / rel
            if not dry and not bak.exists():
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, bak)
        else:
            added.append(str(rel))
        if not dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    if not dry:
        backup.mkdir(exist_ok=True)
        manifest["replaced"] = sorted(set(manifest["replaced"]) | set(replaced))
        manifest["added"] = sorted(set(manifest["added"]) | set(added))
        manifest_path.write_text(json.dumps(manifest, indent=2))
    verb = "Would" if dry else "Did"
    print(f"\n{verb}: add {len(added)} new file(s), replace {len(replaced)} existing file(s), {same} already up to date.")
    for r in replaced:
        print(f"   replaced  {r}")
    if not dry and replaced:
        print(f"   (originals saved in {BACKUP_NAME}/)")


def undo(project):
    backup = project / BACKUP_NAME
    manifest_path = backup / "manifest.json"
    if not manifest_path.exists():
        sys.exit("Nothing to undo: no backup found.")
    manifest = json.loads(manifest_path.read_text())
    for rel in manifest["replaced"]:
        shutil.copy2(backup / rel, project / rel)
    for rel in manifest["added"]:
        f = project / rel
        if f.exists():
            f.unlink()
    for d in ("intelligence", "advanced"):
        shutil.rmtree(project / d, ignore_errors=True)
    print("Restored your V1 files and removed the V2/V5 files.")
    print("Note: V2/V5 database tables (if you ran migrate) stay in MySQL; they are harmless.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", nargs="?", help="path to your V1 project (default: the folder containing this one)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--undo", action="store_true")
    args = ap.parse_args()
    project = find_project(args.project)
    print(f"TransformoDocs project: {project}")
    if args.undo:
        return undo(project)
    install(project, args.dry_run)
    if not args.dry_run:
        print("""
DONE. Now run these commands in your project folder (virtual environment active):

    pip install -r requirements.txt
    python manage.py migrate
    python manage.py seed_initial_data
    python manage.py download_models        (one-time, needs internet; safe to skip and do later)
    python manage.py preflight
    python manage.py runserver --noreload

Full guide: V2_V5_UPDATE/INSTALL.md""")


if __name__ == "__main__":
    main()
