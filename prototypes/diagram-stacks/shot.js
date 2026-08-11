const {launch} = require('./browser');
const idx = Number(process.argv[2] ?? 0), tag = process.argv[3] ?? 'x', theme = process.argv[4] ?? 'dark';
const withHeading = process.argv[5] === 'heading';
(async () => {
  const b = await launch();
  const p = await b.newPage();
  await p.setViewport({width: 1100, height: 1100, deviceScaleFactor: 2});
  await p.goto('file:///tmp/diagram-stack-prototype.html?variant=d2', {waitUntil: 'networkidle0'});
  await p.evaluate(t => document.documentElement.setAttribute('data-theme', t), theme);
  await new Promise(r => setTimeout(r, 250));
  if (withHeading) {
    // capture the h2 together with its card so relative type size is judgeable
    const box = await p.evaluate(i => {
      const cards = [...document.querySelectorAll('.slot.on .card')];
      const c = cards[i]; const h = [...document.querySelectorAll('h2[id]')][i];
      const a = h.getBoundingClientRect(), z = c.getBoundingClientRect();
      return {x: Math.min(a.x,z.x)+scrollX-8, y: a.y+scrollY-8,
              width: Math.max(a.width,z.width)+16, height: (z.bottom-a.top)+16};
    }, idx);
    await p.screenshot({path: `/tmp/shot-${tag}.png`, clip: box});
  } else {
    const cards = await p.$$('.slot.on .card');
    await cards[idx].screenshot({path: `/tmp/shot-${tag}.png`});
  }
  await b.close(); console.log('ok');
})();
