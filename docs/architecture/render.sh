#!/usr/bin/env bash
# Render every diagram in this directory to SVG. Pass --png for PNG as well.
#
#   .d2  -> d2 (pacman -S d2). Each source pins its own layout engine in its
#           `vars.d2-config` block, so no flags are needed here.
#   .py  -> the `diagrams` library over Graphviz, in the virtualenv described
#           in requirements.txt. Skipped with a notice if that venv is absent.
#
# PNG goes through rsvg-convert deliberately: `d2 out.png` downloads and drives
# a headless browser on first use, which takes minutes and needs the network.
# Only the sources and the SVGs are committed; PNG is for slide decks.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
venv="$here/.venv/bin/python"

to_png() {
    [[ ${1:-} == --png ]] || return 0
    rsvg-convert -w 2200 "$2.svg" -o "$2.png"
}

for src in "$here"/*.d2; do
    base=${src%.d2}
    echo "rendering $(basename "$src")"
    d2 --pad 20 "$src" "$base.svg"
    to_png "${1:-}" "$base"
done

for src in "$here"/*.py; do
    base=${src%.py}
    if [[ ! -x $venv ]]; then
        echo "skipping $(basename "$src") — no venv; see requirements.txt"
        continue
    fi
    echo "rendering $(basename "$src")"
    "$venv" "$src"
    to_png "${1:-}" "$base"
done
