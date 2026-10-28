"""Run in the selected indexer's environment; never infer knowledge or media."""

import hashlib
import importlib.metadata
import json
import os
import sys
import tempfile
from pathlib import Path


def relative(root, name):
    return (root / name).resolve().relative_to(root).as_posix()


def crg(root, files, output):
    from code_review_graph.graph import GraphStore
    from code_review_graph.incremental import (
        _load_ignore_patterns,
        _should_ignore,
        incremental_update,
    )
    from code_review_graph.parser import CodeParser

    parser = CodeParser(root)
    patterns = _load_ignore_patterns(root)
    excluded = [name for name in files if _should_ignore(name, patterns)]
    unsupported = [
        name
        for name in files
        if name not in excluded and parser.detect_language(root / name) is None
    ]
    eligible = sorted(set(files) - set(excluded) - set(unsupported))
    store = GraphStore(output)
    try:
        stale = [
            name
            for name in store.get_all_files()
            if relative(root, name) not in eligible
        ]
        # Explicit inventory owns reconciliation; the dependency's full build is tracked-only.
        if stale:
            store.remove_files_permanently(stale)
        if (
            store.has_nodes_for_language("cpp")
            and store.get_metadata("cpp_identity_version") != "1"
        ):
            raise RuntimeError(
                "Legacy C++ identity requires migration; tracked-only rebuild refused"
            )
        result = incremental_update(
            root, store, changed_files=eligible, reconcile_stale=False
        )
        hashes = store.get_file_hashes()
        failed = {
            relative(root, row["file"]): row["error"]
            for row in result.get("errors", [])
        }
        indexed = [
            name
            for name in eligible
            if name not in failed
            and hashes.get(str(root / name))
            == hashlib.sha256((root / name).read_bytes()).hexdigest()
        ]
        return {
            "indexed": indexed,
            "unsupported": unsupported,
            "excluded": excluded,
            "failed": failed,
            "missing": sorted(set(eligible) - set(indexed) - set(failed)),
        }
    finally:
        store.close()


def graphify(root, files, inventory, output):
    os.environ["GRAPHIFY_OUT"] = str(output.parent)
    import networkx as nx
    from graphify.build import build_merge
    from graphify.detect import (
        _is_ignored,
        _is_installed_graphify_file,
        _is_noise_dir,
        _load_graphifyignore,
    )
    from graphify.extract import RUN_ONLY_EXTRACTION_KEYS, _get_extractor, extract

    patterns = _load_graphifyignore(root)
    excluded = {
        name
        for name in inventory
        if _is_ignored(root / name, root, patterns)
        or _is_installed_graphify_file(root / name)
        or any(_is_noise_dir(part) for part in Path(name).parts[:-1])
    }
    unsupported = sorted(
        name
        for name in files
        if name not in excluded and _get_extractor(root / name) is None
    )
    supported = set(files) - excluded - set(unsupported)
    result = extract(
        [root / name for name in sorted(supported)],
        root=root,
        cache_root=root,
        max_workers=2,
    )
    failed = {
        relative(root, name): "Extractor reported failure; see diagnostic log"
        for name in result["failed_sources"]
    }
    skipped = {relative(root, name) for name in result["skipped_sources"]}
    no_symbols = set(skipped)
    for name in list(failed):
        check = _get_extractor(root / name)(root / name)
        if (
            not check.get("error")
            and not check.get("parse_errors")
            and not check.get("nodes")
        ):
            del failed[name]
            no_symbols.add(name)
    indexed = supported - set(failed) - skipped
    extracted = {relative(root, name) for name in result["extracted_sources"]}
    missing = indexed - extracted
    indexed -= missing
    existing = json.loads(output.read_text()) if output.exists() else {}
    prune = set()
    for node in existing.get("nodes", []):
        if node.get("source_file"):
            name = relative(root, node["source_file"])
            if name not in inventory or name in excluded:
                prune.add(name)
    # Failed files retain their last successful nodes. Markdown keeps its semantic tier.
    result["nodes"] = (
        [
            node
            for node in result["nodes"]
            if relative(root, node["source_file"]) not in failed
        ]
        if failed
        else result["nodes"]
    )
    result["edges"] = [
        edge
        for edge in result["edges"]
        if not edge.get("source_file")
        or relative(root, edge["source_file"]) not in failed
    ]
    result = {
        key: value
        for key, value in result.items()
        if key not in RUN_ONLY_EXTRACTION_KEYS
    }
    graph = build_merge(
        [result],
        output,
        prune_sources=sorted(prune),
        root=root,
        ast_sources=sorted((indexed | skipped | (set(inventory) - set(files))) - prune),
        dedup=False,
    )
    atomic_json(output, nx.node_link_data(graph, edges="links"))
    return {
        "indexed": sorted(indexed | skipped),
        "unsupported": unsupported,
        "excluded": sorted(excluded),
        "failed": failed,
        "missing": sorted(missing),
        "no_symbols": sorted(no_symbols),
    }


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    backend, directory, request, destination = sys.argv[1:]
    root = Path(directory).resolve()
    data = json.loads(Path(request).read_text())
    files = data["files"]
    output = Path(data["output"])
    coverage = (
        crg(root, files, output)
        if backend == "crg"
        else graphify(root, files, data["inventory"], output)
    )
    coverage["complete"] = not coverage["failed"] and not coverage["missing"]
    coverage["version"] = importlib.metadata.version(
        "code-review-graph" if backend == "crg" else "graphifyy"
    )
    atomic_json(Path(destination), coverage)
    print(
        json.dumps(
            {
                key: len(value) if isinstance(value, (list, dict)) else value
                for key, value in coverage.items()
            }
        )
    )


if __name__ == "__main__":
    main()
