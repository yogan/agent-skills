// THROWAWAY — exact clipping check. The static SVG version could not do this: it ignored
// transform="translate()" (mermaid uses it everywhere) and could not see foreignObject text.
// The browser resolves every transform, so compare each painted element's client rect against
// the <svg>'s own client rect.
const {launch} = require('./browser');
(async () => {
  const b = await launch();
  const p = await b.newPage();
  await p.setViewport({width:1100, height:1000});
  let bad = 0;
  for (const variant of ['d2','gv','mmd']) {
    await p.goto(`file:///tmp/diagram-stack-prototype.html?variant=${variant}`, {waitUntil:'networkidle0'});
    const rows = await p.evaluate(() => {
      const out = [];
      document.querySelectorAll('h2[id]').forEach((h,i) => {
        const card = document.querySelectorAll('.slot.on .card')[i]; if (!card) return;
        const svg = card.querySelector('svg'); if (!svg) return;
        const S = svg.getBoundingClientRect();
        // What actually clips: the CARD (overflow:hidden). The <svg> is overflow:visible so a
        // callout's shadow may legitimately bleed into the card's padding. Diagram geometry is
        // still held to the svg box; only the callout+shadow gets the card as its boundary.
        const cs = getComputedStyle(card);
        const CARD = card.getBoundingClientRect();
        const inner = {left: CARD.left + parseFloat(cs.paddingLeft) * 0,
                       right: CARD.right, top: CARD.top, bottom: CARD.bottom};
        const over = {left:0, right:0, top:0, bottom:0};
        // A callout carries a css drop-shadow, which getBoundingClientRect does NOT include.
        // Reserve its reach, otherwise a callout flush with the edge measures as fitting while
        // its glow is cut off — exactly the bug this check exists to catch.
        const SHADOW = 8;
        const calloutGroups = [...svg.querySelectorAll('foreignObject')].map(f => f.closest('g'));
        svg.querySelectorAll('rect,text,foreignObject,path,ellipse').forEach(el => {
          let r = el.getBoundingClientRect();
          if (!r.width && !r.height) return;
          if (r.width >= S.width * 0.95 && r.height >= S.height * 0.95) return; // background
          let B = S;
          if (calloutGroups.some(g => g && g.contains(el))) {
            r = {left:r.left-SHADOW, right:r.right+SHADOW, top:r.top-SHADOW, bottom:r.bottom+SHADOW};
            B = inner;   // shadow may bleed past the svg, but never past the card
          }
          over.left   = Math.max(over.left,   B.left - r.left);
          over.right  = Math.max(over.right,  r.right - B.right);
          over.top    = Math.max(over.top,    B.top - r.top);
          over.bottom = Math.max(over.bottom, r.bottom - B.bottom);
        });
        out.push({id:h.id, over});
      });
      return out;
    });
    for (const {id, over} of rows) {
      const hit = Object.entries(over).filter(([,v]) => v > 1);
      if (hit.length) bad++;
      console.log(`${(variant+'--'+id).padEnd(20)} ${hit.length ? 'CLIPPED' : 'ok'}`.padEnd(30) +
        (hit.map(([k,v]) => `${k} ${v.toFixed(0)}px`).join('  ') || ''));
    }
  }
  console.log(`\n${bad} clipped`);
  await b.close();
})();
