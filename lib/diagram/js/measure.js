// Browser-side measurement for the clipping gate and the callout placement search.
//
// This script MEASURES and nothing else. Every decision — which anchors to try, how to
// weight an overlap, which candidate wins — is made in Python (place.py, gates/clipping.py),
// because that logic is worth unit-testing and a headless Chrome is a miserable place to
// test anything. So the contract here is deliberately dumb: read a batch of jobs from
// stdin, hand back numbers on stdout.
//
// Why a browser at all, when the rest of the gates read the SVG directly:
//   * getBoundingClientRect() resolves every transform="translate()" for us. A static
//     checker has to reimplement that, and the prototype's attempt at it produced false
//     positives on everything.
//   * callout text is a <foreignObject> — real HTML, laid out by the browser using the
//     page's own CSS. Nothing outside a browser knows how big it ends up.
//   * a CSS drop-shadow's spread is invisible to the DOM geometry API, so it has to be
//     reserved explicitly (see SHADOW below). That was a real bug: a callout flush with
//     the edge measured as fitting while its glow was being cut off, and the gate said 0
//     clipped and was wrong.
//
// stdin:  {viewport: {width, height}, shadow: 8, weights: {...}, jobs: [{key, html}]}
// stdout: {results: [{key, svg, card, callouts: [...], overflow: {...}, offenders: [...]}]}
'use strict';

const {execSync} = require('child_process');
const fs = require('fs');

const CANDIDATES = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
];

function fail(message) {
  process.stderr.write(message + '\n');
  process.exit(2);
}

function corePath() {
  if (process.env.PUPPETEER_CORE) return process.env.PUPPETEER_CORE;
  let root;
  try {
    root = execSync('npm root -g', {encoding: 'utf8'}).trim();
  } catch {
    fail('could not run `npm root -g` to locate puppeteer-core. Install it with\n' +
         '  npm i -g puppeteer-core\n' +
         'or point PUPPETEER_CORE at an existing copy.');
  }
  const path = `${root}/puppeteer-core`;
  if (!fs.existsSync(path)) {
    // Deliberately puppeteer-CORE: the full `puppeteer` package downloads its own
    // ~550MB Chromium, where core is 29MB and drives the browser already installed.
    fail(`puppeteer-core not found at ${path}. Install it with\n` +
         '  npm i -g puppeteer-core\n' +
         'or point PUPPETEER_CORE at an existing copy.');
  }
  return path;
}

function chromePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) return process.env.PUPPETEER_EXECUTABLE_PATH;
  const hit = CANDIDATES.find(p => fs.existsSync(p));
  if (!hit) {
    fail('no system Chrome/Chromium/Edge found. Set PUPPETEER_EXECUTABLE_PATH to a\n' +
         'browser binary, or install a bundled one with: npm i -g puppeteer');
  }
  return hit;
}

// Runs inside the page. Returns geometry for one rendered diagram.
function measureInPage(shadow, weights) {
  const svg = document.querySelector('.diagram svg');
  const card = document.querySelector('.diagram');
  if (!svg || !card) return {error: 'no .diagram svg in the harness page'};

  const S = svg.getBoundingClientRect();
  const cardBox = card.getBoundingClientRect();
  const rect = r => ({left: r.left, top: r.top, right: r.right, bottom: r.bottom,
                      width: r.width, height: r.height});
  const area = r => Math.max(0, r.width) * Math.max(0, r.height);
  const intersect = (a, b) =>
    Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) *
    Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
  const outside = (inner, bound) => ({
    left: Math.max(0, bound.left - inner.left),
    right: Math.max(0, inner.right - bound.right),
    top: Math.max(0, bound.top - inner.top),
    bottom: Math.max(0, inner.bottom - bound.bottom),
  });
  const total = o => o.left + o.right + o.top + o.bottom;

  const painted = [...svg.querySelectorAll('rect,text,path,ellipse,circle,polygon,foreignObject')];
  const calloutGroups = [...svg.querySelectorAll('foreignObject')]
    .map(fo => fo.closest('g')).filter(Boolean);

  const callouts = calloutGroups.map(group => {
    const R = group.getBoundingClientRect();
    // Reserve the drop-shadow's reach: the DOM geometry API does not include it.
    const grown = {left: R.left - shadow, top: R.top - shadow,
                   right: R.right + shadow, bottom: R.bottom + shadow,
                   width: R.width + 2 * shadow, height: R.height + 2 * shadow};
    let overlap = 0;
    for (const el of painted) {
      if (group.contains(el)) continue;
      const r = el.getBoundingClientRect();
      if (!r.width && !r.height) continue;
      // A container or background rect covering half the canvas is not something a
      // callout can meaningfully "cover" — ignoring it stops the optimiser from
      // optimising for the wrong thing.
      if (area(r) >= area(S) * 0.5) continue;
      const weight = weights[el.tagName.toLowerCase()] ?? 1;
      overlap += intersect(grown, r) * weight;
    }
    return {
      box: rect(R),
      // vs the SVG box: what the placement search minimises, since d2 reserves no canvas
      // space for a callout and an anchor that overflows is simply the wrong anchor.
      clip: total(outside(grown, S)),
      // vs the CARD: what actually clips on the real page (overflow:hidden). The shadow is
      // allowed to bleed into the card's padding; the geometry is not allowed past the card.
      clipVsCard: total(outside(grown, cardBox)),
      overflowVsCard: outside(grown, cardBox),
      overlap,
    };
  });

  // The clipping gate proper: every painted element against the boundary that really
  // clips it. Non-callout geometry is held to the svg box; a callout gets the card,
  // because its shadow may legitimately bleed into the card's padding.
  const worst = {left: 0, right: 0, top: 0, bottom: 0};
  const offenders = [];
  for (const el of painted) {
    let r = el.getBoundingClientRect();
    if (!r.width && !r.height) continue;
    if (r.width >= S.width * 0.95 && r.height >= S.height * 0.95) continue;  // background
    const inCallout = calloutGroups.some(g => g.contains(el));
    let bound = S;
    if (inCallout) {
      r = {left: r.left - shadow, top: r.top - shadow,
           right: r.right + shadow, bottom: r.bottom + shadow};
      bound = cardBox;
    }
    const over = outside(r, bound);
    for (const side of ['left', 'right', 'top', 'bottom']) {
      if (over[side] > worst[side]) worst[side] = over[side];
    }
    if (total(over) > 1 && offenders.length < 8) {
      offenders.push({
        tag: el.tagName.toLowerCase(),
        text: (el.textContent || '').trim().slice(0, 30),
        callout: inCallout,
        over,
      });
    }
  }

  return {svg: rect(S), card: rect(cardBox), callouts, overflow: worst, offenders};
}

(async () => {
  let input = '';
  for await (const chunk of process.stdin) input += chunk;
  let job;
  try {
    job = JSON.parse(input);
  } catch (e) {
    fail(`could not parse the job from stdin: ${e.message}`);
  }
  const {viewport = {width: 1200, height: 1000}, shadow = 8, weights = {}, jobs = []} = job;

  const puppeteer = require(corePath());
  const browser = await puppeteer.launch({headless: true, executablePath: chromePath()});
  try {
    const page = await browser.newPage();
    await page.setViewport(viewport);
    const results = [];
    for (const one of jobs) {
      await page.setContent(one.html, {waitUntil: 'load'});
      // The callout text is HTML in a foreignObject; without waiting for fonts its box is
      // measured against fallback metrics and every number here is subtly wrong.
      await page.evaluate(() => document.fonts && document.fonts.ready);
      const measured = await page.evaluate(measureInPage, shadow, weights);
      results.push({key: one.key, ...measured});
    }
    process.stdout.write(JSON.stringify({results}));
  } finally {
    await browser.close();
  }
})().catch(e => fail(`browser measurement failed: ${e && e.stack || e}`));
