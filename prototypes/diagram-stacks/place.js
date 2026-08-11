// THROWAWAY — automatic tooltip placement.
//
// d2 will NOT place a permanently-visible callout for you: `near` takes one of 8 fixed anchors
// relative to the shape and d2 does no overlap avoidance (it does not even grow the canvas to
// fit the callout). So brute-force it: render all 8 anchors, measure the callout against every
// other painted element IN A BROWSER, and keep the anchor that is not clipped and covers least.
const {execFileSync} = require('child_process');
const fs = require('fs');
const {launch} = require('./browser');
const ANCHORS = ['top-left','top-center','top-right','center-left','center-right',
                 'bottom-left','bottom-center','bottom-right'];
const PRELUDE = fs.readFileSync('style/d2-warm.prelude', 'utf8');
const CSS = `foreignObject{overflow:visible}
 foreignObject .md{display:flex;align-items:center;height:100%;font-family:system-ui,sans-serif}
 foreignObject .md p{margin:0;line-height:1.2;font-size:12.5px;white-space:nowrap}`;

function render(src) {
  fs.writeFileSync('/tmp/place.d2', PRELUDE + src);
  execFileSync('d2', ['--pad','8','--theme','0','/tmp/place.d2','/tmp/place.svg'], {stdio:'ignore'});
  return fs.readFileSync('/tmp/place.svg','utf8');
}

(async () => {
  const b = await launch();
  const page = await b.newPage();
  await page.setViewport({width:1200,height:1000});

  const score = async (svg) => page.evaluate((markup, css) => {
    document.body.innerHTML = `<style>${css}</style><div>${markup}</div>`;
    const svgEl = document.querySelector('svg');
    const S = svgEl.getBoundingClientRect();
    const area = r => Math.max(0,r.width) * Math.max(0,r.height);
    const inter = (a,c) => Math.max(0, Math.min(a.right,c.right)-Math.max(a.left,c.left)) *
                           Math.max(0, Math.min(a.bottom,c.bottom)-Math.max(a.top,c.top));
    let clipped = 0, overlap = 0;
    svgEl.querySelectorAll('foreignObject').forEach(fo => {
      const g = fo.closest('g');
      const R = g.getBoundingClientRect();
        // getBoundingClientRect EXCLUDES the css drop-shadow spread, so a callout flush with
        // the edge measures as fitting while its glow is cut. Reserve the shadow's reach.
        const SHADOW = 8;
        const C = {left:R.left-SHADOW, right:R.right+SHADOW, top:R.top-SHADOW,
                   bottom:R.bottom+SHADOW, width:R.width+2*SHADOW, height:R.height+2*SHADOW};
      clipped += Math.max(0, S.left-C.left) + Math.max(0, C.right-S.right)
               + Math.max(0, S.top-C.top) + Math.max(0, C.bottom-S.bottom);
      // weight by what actually hurts: covering a LABEL makes it unreadable, covering an
      // edge obscures a relationship, covering a big container rect is harmless.
      const W = {text: 6, foreignObject: 6, path: 2, ellipse: 0.3, rect: 0.3};
      svgEl.querySelectorAll('rect,text,path,ellipse,foreignObject').forEach(el => {
        if (g.contains(el)) return;
        const r = el.getBoundingClientRect();
        if (area(r) >= area(S) * 0.5) return;   // container/background: not "covering" anything
        overlap += inter(C, r) * (W[el.tagName.toLowerCase()] ?? 1);
      });
    });
    return {clipped, overlap};
  }, svg, CSS);

  for (const name of process.argv.slice(2)) {
    const file = `src/d2/${name}.d2`;
    let src = fs.readFileSync(file, 'utf8');
    const count = (src.match(/tooltip\.near:/g) || []).length;
    for (let i = 0; i < count; i++) {
      let best = null;
      for (const a of ANCHORS) {
        let n = -1;
        const trial = src.replace(/tooltip\.near: *\S+/g, m => (++n === i ? `tooltip.near: ${a}` : m));
        let s;
        try { s = render(trial); } catch { continue; }
        const {clipped, overlap} = await score(s);
        // clipping is disqualifying, not a trade-off: a cut-off callout is worse than one
        // that merely overlaps something. Only compare overlap among clip-free candidates.
        const total = clipped * 1e6 + overlap;
        if (!best || total < best.total) best = {a, total, clipped, overlap, trial};
      }
      if (best) {
        src = best.trial;
        console.log(`  ${name}[${i}] -> ${best.a.padEnd(14)} clip ${best.clipped.toFixed(0).padStart(4)}px  overlap ${best.overlap.toFixed(0).padStart(6)}px²`);
      }
    }
    fs.writeFileSync(file, src);
  }
  await b.close();
})();
