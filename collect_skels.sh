#!/usr/bin/env bash
# Collect all *-skel.yaml and *-skel.yml files from subdirectories into cwd.
find . -mindepth 2 \( -name "*-skel.yaml" -o -name "*-skel.yml" \) -exec cp {} . \;
