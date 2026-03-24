#!/usr/bin/env python3
"""
make_skeletons.py — recurse a directory tree, find all .yaml/.yml files,
and write a skeleton version next to each one as <name>-skel.yaml/.yml.

Skeleton rules:
  - Scalar values are replaced with <str>, <int>, <float>, <bool>, or <null>.
  - Lists are collapsed to one skeleton entry per distinct key-set found
    across all items in that list (so fields that only appear in some items
    are not silently dropped).
  - Dicts are recursed normally.
  - Files already named *-skel.yaml / *-skel.yml are skipped.

Usage:
    python make_skeletons.py [ROOT_DIR]

ROOT_DIR defaults to the current directory.
"""

import sys
import os
import yaml


# ---------------------------------------------------------------------------
# Skeleton builder
# ---------------------------------------------------------------------------

def scalar_tag(value):
    if value is None:
        return "<null>"
    if isinstance(value, bool):
        return "<bool>"
    if isinstance(value, int):
        return "<int>"
    if isinstance(value, float):
        return "<float>"
    return "<str>"


def skeleton(node):
    """Recursively replace scalar values with type tags."""
    if isinstance(node, dict):
        return {k: skeleton(v) for k, v in node.items()}
    if isinstance(node, list):
        return skeleton_list(node)
    return scalar_tag(node)


def skeleton_list(lst):
    """
    Collapse a list to one representative skeleton entry per distinct key-set.
    For lists of scalars, returns a single-element list with the type tag.
    For mixed lists, groups by key-set and merges keys across the group.
    """
    if not lst:
        return []

    # Separate dict items from scalar items
    dict_items = [item for item in lst if isinstance(item, dict)]
    scalar_items = [item for item in lst if not isinstance(item, dict)]

    result = []

    # One placeholder for scalars if any exist
    if scalar_items:
        result.append(scalar_tag(scalar_items[0]))

    if dict_items:
        # Group by frozenset of top-level keys
        groups = {}
        for item in dict_items:
            key = frozenset(item.keys())
            if key not in groups:
                groups[key] = []
            groups[key].append(item)

        for items_in_group in groups.values():
            # Merge all items in the group so every key is represented
            merged = {}
            for item in items_in_group:
                for k, v in item.items():
                    if k not in merged:
                        merged[k] = v
                    # If the existing value is None/scalar and new one is richer, prefer richer
                    elif isinstance(v, (dict, list)) and not isinstance(merged[k], (dict, list)):
                        merged[k] = v
            result.append(skeleton(merged))

    return result


# ---------------------------------------------------------------------------
# YAML dumper that preserves key order and uses block style
# ---------------------------------------------------------------------------

class SkeletonDumper(yaml.Dumper):
    pass


# Represent None as the literal string <null> (already a string by the time
# we dump, since scalar_tag returns strings — but just in case)
def _str_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


SkeletonDumper.add_representer(str, _str_representer)


def dump_skeleton(data):
    return yaml.dump(
        data,
        Dumper=SkeletonDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )


# ---------------------------------------------------------------------------
# File discovery and processing
# ---------------------------------------------------------------------------

def skel_path(path):
    """Return the skeleton output path for a given yaml file path."""
    root, ext = os.path.splitext(path)
    return root + "-skel" + ext


def is_skel_file(path):
    name = os.path.basename(path)
    stem, _ = os.path.splitext(name)
    return stem.endswith("-skel")


def process_file(path):
    out_path = skel_path(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"  SKIP (parse error): {e}")
        return False

    if data is None:
        print(f"  SKIP (empty file)")
        return False

    skel = skeleton(data)

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(dump_skeleton(skel))
    except Exception as e:
        print(f"  ERROR writing {out_path}: {e}")
        return False

    print(f"  -> {os.path.basename(out_path)}")
    return True


def run(root_dir):
    root_dir = os.path.abspath(root_dir)
    print(f"Scanning: {root_dir}\n")

    found = 0
    written = 0

    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Skip hidden directories
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        yaml_files = [
            f for f in filenames
            if f.lower().endswith((".yaml", ".yml"))
            and not is_skel_file(f)
        ]

        if not yaml_files:
            continue

        rel_dir = os.path.relpath(dirpath, root_dir)
        print(f"[{rel_dir}]")

        for filename in sorted(yaml_files):
            found += 1
            filepath = os.path.join(dirpath, filename)
            print(f"  {filename}", end="")
            if process_file(filepath):
                written += 1

    print(f"\nDone. {found} file(s) found, {written} skeleton(s) written.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    if not os.path.isdir(root):
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        sys.exit(1)
    run(root)
