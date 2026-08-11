// Joint (exhaustive) search over BOTH callouts of one diagram — greedy per-callout cannot
// escape a configuration where the two only fit in a particular combination.
const {execFileSync} = require('child_process');
const fs = require('fs');
const {launch} = require('./browser');
const name = process.argv[2];
const A = ['top-left','top-center','top-right','center-left','center-right',
           'bottom-left','bottom-center','bottom-right'];
const PRE = fs.readFileSync('style/d2-warm.prelude','utf8');
const CSS = `foreignObject{overflow:visible}
 foreignObject .md{display:flex;align-items:center;height:100%;font-family:system-ui,sans-serif}
 foreignObject .md p{margin:0;line-height:1.2;font-size:12.5px;white-space:nowrap}`;
const base = fs.readFileSync(`src/d2/${name}.d2`,'utf8');
const set = (src, vals) => { let n=-1; return src.replace(/tooltip\.near: *\S+/g, () => `tooltip.near: ${vals[++n]}`); };

(async () => {
  const b = await launch();
  const p = await b.newPage();
  await p.setViewport({width:1200,height:1000});
  let best = null;
  for (const a1 of A) for (const a2 of A) {
    fs.writeFileSync('/tmp/j.d2', PRE + set(base, [a1,a2]));
    try { execFileSync('d2',['--pad','8','--theme','0','/tmp/j.d2','/tmp/j.svg'],{stdio:'ignore'}); }
    catch { continue; }
    const svg = fs.readFileSync('/tmp/j.svg','utf8');
    const s = await p.evaluate((m,css) => {
      document.body.innerHTML = `<style>${css}</style><div>${m}</div>`;
      const sv = document.querySelector('svg'), S = sv.getBoundingClientRect();
      const ar = r => Math.max(0,r.width)*Math.max(0,r.height);
      const it = (a,c) => Math.max(0,Math.min(a.right,c.right)-Math.max(a.left,c.left)) *
                          Math.max(0,Math.min(a.bottom,c.bottom)-Math.max(a.top,c.top));
      let clip=0, ov=0;
      const W={text:6,foreignObject:6,path:2,ellipse:.3,rect:.3};
      sv.querySelectorAll('foreignObject').forEach(fo => {
        const g=fo.closest('g'), R=g.getBoundingClientRect();
        // getBoundingClientRect EXCLUDES the css drop-shadow spread, so a callout flush with
        // the edge measures as fitting while its glow is cut. Reserve the shadow's reach.
        const SHADOW=8, C={left:R.left-SHADOW,right:R.right+SHADOW,top:R.top-SHADOW,bottom:R.bottom+SHADOW,width:R.width+2*SHADOW,height:R.height+2*SHADOW};
        clip += Math.max(0,S.left-C.left)+Math.max(0,C.right-S.right)
              + Math.max(0,S.top-C.top)+Math.max(0,C.bottom-S.bottom);
        sv.querySelectorAll('rect,text,path,ellipse,foreignObject').forEach(el => {
          if (g.contains(el)) return;
          const r=el.getBoundingClientRect();
          if (ar(r) >= ar(S)*0.5) return;
          ov += it(C,r) * (W[el.tagName.toLowerCase()] ?? 1);
        });
      });
      return {clip, ov};
    }, svg, CSS);
    const total = s.clip*1e6 + s.ov;
    if (!best || total < best.total) best = {a1,a2,total,...s};
  }
  console.log(`  best: ${best.a1} + ${best.a2}   clip ${best.clip.toFixed(0)}px  overlap ${best.ov.toFixed(0)}px²`);
  fs.writeFileSync(`src/d2/${name}.d2`, set(base,[best.a1,best.a2]));
  await b.close();
})();
