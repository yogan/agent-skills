#!/bin/bash
# Prototype build — D2 only (the graphviz and mermaid arms were removed once the engine
# decision was made; see HANDOFF.md). Renders the 5 static diagrams plus the animated one.
cd "$(dirname "$0")"
mkdir -p out; rm -f out/*.svg out/*.err
TYPES="arch sequence er class state"

for t in $TYPES; do
  cat style/d2-warm.prelude "src/d2/$t.d2" > /tmp/d2src.d2
  d2 --pad 8 --theme 0 /tmp/d2src.d2 "out/d2--$t.svg" >/dev/null 2>&1 || echo "FAIL d2 $t"
done

cat style/d2-warm.prelude src/d2/animated.d2 > /tmp/anim.d2
d2 --pad 8 --theme 0 --animate-interval 1800 /tmp/anim.d2 out/d2--animated.svg >/dev/null 2>&1 \
  || echo "FAIL d2 animated"

echo "--- rendered: $(ls out/*.svg | wc -l | tr -d ' ') ---"
