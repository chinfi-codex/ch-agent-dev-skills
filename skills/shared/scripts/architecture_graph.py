#!/usr/bin/env python3
"""Read legacy ARCHITECTURE tables and sharded architecture graph records.

Graph files live in docs/architecture/*.json. This reader is deliberately
read-only: Markdown remains a generated compatibility view, never a second
source of truth once graph shards exist.
"""
from __future__ import annotations
import argparse, hashlib, json, re, sys
from pathlib import Path

STATUSES = {"current", "release_only", "superseded", "retired", "needs_verification", "legacy"}


def split_row(line):
    return [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def legacy_rows(repo):
    path = repo / "docs" / "ARCHITECTURE.md"
    if not path.exists(): return []
    lines, active, out = path.read_text(encoding="utf-8").splitlines(), False, []
    for line in lines:
        if line.startswith("## 接口现行态"): active = True; continue
        if active and line.startswith("## "): break
        if active and line.strip().startswith("|"):
            cells = split_row(line)
            if all(set(cell) <= {"-", ":", " "} for cell in cells): continue
            if cells and (cells[0].startswith("接口（") or cells[0].lower() in {"interface", "接口"}): continue
            if len(cells) >= 5 and cells[0] not in {"接口", "…"}: out.append(cells)
    return out


def legacy_records(repo):
    records=[]
    for row in legacy_rows(repo):
        # Identity is the interface name plus its source symbol. Mutable
        # annotations (contract, spec, date) must not change graph references.
        branch = row[-1] if len(row) > 5 else ""
        raw="\t".join((row[0], row[2] if len(row)>2 else "", branch))
        ident="legacy."+hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        code=row[2] if len(row)>2 else ""
        match=re.match(r"([^:]+)(?::(.*))?$",code)
        source={"path":match.group(1) if match else code,"symbol":match.group(2) if match else ""}
        records.append({"id":ident,"module":"legacy","kind":"interface","name":row[0],"status":"legacy","version":None,"source":source,"verification":"legacy table annotation only","last_verified":"unknown","legacy_row":row,"relations":[],"supersedes":[]})
    return records


def read_records(repo):
    """Merge both formats by stable legacy row ID; graph records override annotations."""
    legacy = legacy_records(repo)
    merged={row["id"]:row for row in legacy}
    graph, errors=load_graph(repo)
    legacy_ids = [row["id"] for row in legacy]
    if len(legacy_ids) != len(set(legacy_ids)):
        errors.append("legacy table has duplicate interface/source/branch identities")
    merged.update({row.get("id"):row for row in graph if row.get("id")})
    return list(merged.values()), errors


def load_graph(repo):
    folder = repo / "docs" / "architecture"
    records, errors = [], []
    if not folder.exists(): return records, errors
    for path in sorted(folder.glob("*.json")):
        try: doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.relative_to(repo)}: invalid JSON ({type(exc).__name__})"); continue
        if not isinstance(doc, dict) or doc.get("schema") != "pandu.architecture-graph/v1" or not isinstance(doc.get("records"), list):
            errors.append(f"{path.relative_to(repo)}: unsupported graph schema/records"); continue
        for row in doc["records"]:
            if not isinstance(row, dict): errors.append(f"{path.name}: record must be object"); continue
            records.append(row)
    return records, errors


def check(repo):
    records, errors = load_graph(repo)
    ids = {}
    supersedes_by_id = {}
    for row in records:
        ident = row.get("id")
        if not isinstance(ident, str) or not ident: errors.append("record has empty id"); continue
        if ident in ids: errors.append(f"duplicate id: {ident}")
        ids[ident] = row
        row_status = row.get("status")
        if not isinstance(row_status, str) or row_status not in STATUSES: errors.append(f"{ident}: invalid status")
        for key in ("module", "responsibility", "kind", "source", "verification", "evidence", "last_verified", "last_verified_version"):
            if not row.get(key): errors.append(f"{ident}: missing {key}")
        source = row.get("source", {})
        verified = isinstance(row_status, str) and row_status in {"current", "release_only"}
        if verified:
            if not isinstance(source, dict) or not isinstance(source.get("path"), str) or not source["path"] or not isinstance(source.get("sha256"), str) or not row.get("verified_commit"):
                errors.append(f"{ident}: verified record requires source.path, source.sha256 and verified_commit")
                continue
            try:
                import subprocess
                commit_path = subprocess.run(["git", "show", f"{row['verified_commit']}:{source['path']}"], cwd=repo, capture_output=True, check=True).stdout
                if hashlib.sha256(commit_path).hexdigest() != source["sha256"]:
                    errors.append(f"{ident}: source hash does not match verified_commit: {source['path']}")
                commit_text = commit_path.decode("utf-8", errors="replace")
                if source.get("symbol") and source["symbol"] not in commit_text:
                    errors.append(f"{ident}: source symbol missing at verified_commit: {source['symbol']}")
                if row.get("status") == "current":
                    source_path = repo / source["path"]
                    if not source_path.is_file(): errors.append(f"{ident}: current source path missing: {source['path']}")
                    else:
                        current = source_path.read_bytes()
                        if hashlib.sha256(current).hexdigest() != source["sha256"]: errors.append(f"{ident}: stale source hash: {source['path']}")
                    changed = subprocess.run(["git", "diff", "--quiet", row["verified_commit"], "HEAD", "--", source["path"]], cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
                    if changed != 0: errors.append(f"{ident}: source changed since verified_commit: {source['path']}")
            except (OSError, subprocess.CalledProcessError): errors.append(f"{ident}: cannot verify source at verified_commit")
    for row in records:
        supersedes = row.get("supersedes", [])
        relations = row.get("relations", [])
        if not isinstance(supersedes, list): errors.append(f"{row.get('id')}: supersedes must be a list"); supersedes=[]
        if not isinstance(relations, list): errors.append(f"{row.get('id')}: relations must be a list"); relations=[]
        row_id = row.get("id")
        if not isinstance(row_id, str) or row_id not in ids: continue
        valid_supersedes = []
        for target in supersedes:
            if not isinstance(target, str): errors.append(f"{row.get('id')}: invalid supersedes target {target}"); continue
            if target not in ids: errors.append(f"{row.get('id')}: supersedes missing id {target}")
            else: valid_supersedes.append(target)
        supersedes_by_id[row_id] = valid_supersedes
        for target in relations:
            if not isinstance(target, dict) or not isinstance(target.get("target"), str): errors.append(f"{row.get('id')}: malformed relation"); continue
            if target["target"] not in ids: errors.append(f"{row.get('id')}: dangling relation {target['target']}")
    # Supersedes must form a DAG.
    visiting, visited = set(), set()
    def visit(ident):
        if ident in visiting: errors.append(f"supersedes cycle at {ident}"); return
        if ident in visited: return
        visiting.add(ident)
        for nxt in supersedes_by_id.get(ident, []):
            if nxt in ids: visit(nxt)
        visiting.remove(ident); visited.add(ident)
    for ident in ids: visit(ident)
    return records, errors


def select(records, *, module=None, interface=None, version=None, status=None):
    out=[]
    for row in records:
        if module and row.get("module") != module: continue
        if interface and interface not in (row.get("name", "") + " " + row.get("id", "")): continue
        if status and row.get("status") != status: continue
        if version:
            applies = row.get("applies_to") or []
            if row.get("version") != version and version not in applies and "*" not in applies: continue
        if status is None and row.get("status") in {"needs_verification", "superseded", "retired"}: continue
        if status is None and row.get("status") == "release_only" and not version: continue
        out.append(row)
    return out


def compatibility_view(records):
    rows=["# Architecture compatibility view", "", "Generated from graph shards; do not edit.", "", "| ID | Module | Name | Status | Version | Source |", "| --- | --- | --- | --- | --- | --- |"]
    for r in records:
        src=r.get("source", {})
        vals=[r.get("id", ""),r.get("module", ""),r.get("name", ""),r.get("status", ""),r.get("version", ""),src.get("path", "") if isinstance(src,dict) else ""]
        rows.append("| " + " | ".join(str(x).replace("|", "\\|") for x in vals) + " |")
    return "\n".join(rows)+"\n"


def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument("--repo",type=Path,default=Path.cwd()); ap.add_argument("--check",action="store_true"); ap.add_argument("--module"); ap.add_argument("--interface"); ap.add_argument("--version"); ap.add_argument("--status"); ap.add_argument("--compat-out",type=Path); a=ap.parse_args(argv)
    graph_records, errors = check(a.repo)
    if a.check:
        for e in errors: print("FAIL "+e)
        print(f"graph records={len(graph_records)} errors={len(errors)}")
        return 1 if errors else 0
    if errors: print("\n".join(errors),file=sys.stderr); return 1
    records, _ = read_records(a.repo)
    selected=select(records,module=a.module,interface=a.interface,version=a.version,status=a.status)
    if a.compat_out: a.compat_out.write_text(compatibility_view(selected),encoding="utf-8")
    else: print(json.dumps(selected,ensure_ascii=False,indent=2))
    return 0
if __name__ == "__main__": raise SystemExit(main())
