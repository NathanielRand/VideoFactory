#!/bin/sh
cd "$(dirname "$0")" || exit 1
node start.mjs || { echo; echo "Press Enter to close."; read -r _; }
