// Browser-side measurement for the clipping gate and the callout placement search, plus the
// rasteriser that turns a finished standalone diagram into a PNG.
//
// This script MEASURES and DRAWS; it decides nothing. Every decision — which anchors to try, how to
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
// Rasterising lives here rather than in its own script because it needs the same browser for
// the same reason (a callout is real HTML), and because macOS cannot be trusted with our SVG:
// Quick Look — the engine behind Preview's SVG support — ignores the canvas and crops the
// drawing to a square, so the reference state machine lost its callout and two of its states.
// A PNG we rendered ourselves is what the default image viewer can actually be given.
//
// stdin:  {viewport: {width, height}, shadow: 8, weights: {...}, jobs: [{key, html}],
//          shots: [{key, html, out, width, height, scale}]}
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

  // Text nobody can read. This is the one defect that cannot be traded against anything: a
  // diagram exists to say something, and an unreadable word says nothing.
  //
  // CONTAINMENT decides it, not paint order. Paint order was the first attempt and it missed
  // the case that prompted this: on the reference ER, `1 doc : n sessions` overlapped the
  // `presence_sessions` table by 3px, but the LABEL painted second — so it was technically on
  // top, and technically fine, while its leading glyph sat in grey on a dark purple header and
  // could not be read. Occluded and overprinted are the same defect to a reader, so both count.
  //
  // A shape that FULLY CONTAINS the text is its background and is skipped — that is how a table
  // cell's own row, a node's own box, and the canvas backdrop stay out of it. Anything a label
  // only partly overlaps is either covering it or being covered by it.
  //
  // Fills only: a hairline border crossing a descender is not what this is for, and counting
  // strokes made every edge label that merely touched an arrow look buried.
  const encloses = (o, r) => o.left <= r.left + 1 && o.right >= r.right - 1 &&
                             o.top <= r.top + 1 && o.bottom >= r.bottom - 1;
  // Relative luminance and WCAG ratio, same formulas as gates/contrast.py — duplicated here
  // rather than shipped across the process boundary, because the decision needs the COMPUTED
  // colours and only the browser has those.
  const lum = css => {
    const m = (css || '').match(/[\d.]+/g);
    if (!m || m.length < 3) return null;
    const ch = m.slice(0, 3).map(v => {
      const c = Math.min(1, Math.max(0, +v / 255));
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  };
  const ratio = (a, b) => {
    const [x, y] = [lum(a), lum(b)];
    if (x === null || y === null) return null;
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const AA = 4.5;

  const order = new Map(painted.map((el, i) => [el, i]));
  const hidden = [];
  let hiddenArea = 0;
  // Split by CAUSE, because the two have different fixes and different owners. A label buried
  // by the drawing itself is a spacing problem, which `render._pick_layout` can escalate its way
  // out of. A label buried by a CALLOUT is an anchor problem, which `place` owns and already
  // scores. Reporting one number for both made the layout search widen the whole diagram to
  // solve something only the anchor could — the reference state machine grew 45px for a callout
  // that the placement pass then moved anyway.
  let byLayout = 0;
  painted.filter(el => el.tagName.toLowerCase() === 'text').forEach(el => {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const textFill = getComputedStyle(el).fill;
    let covered = 0;
    let why = '';
    for (const other of painted) {
      if (other === el || other.contains(el) || el.contains(other)) continue;
      if (other.tagName.toLowerCase() === 'text') continue;
      const fill = getComputedStyle(other).fill;
      if (!fill || fill === 'none' || fill === 'rgba(0, 0, 0, 0)') continue;
      const o = other.getBoundingClientRect();
      if (!o.width || !o.height || encloses(o, r)) continue;
      const hit = intersect(r, o);
      if (hit <= 1) continue;
      // Two different defects, and telling them apart is what stops this flagging things a
      // reader can read perfectly well:
      //
      //   * the shape paints AFTER the text, so the text is genuinely underneath it. Opaque
      //     geometry on top of a word hides it whatever the colours are.
      //   * the shape paints BEFORE, so the text is on top of it — readable or not depending
      //     on contrast. `GraphQL` straying onto a pale container is fine; `1 doc :` straying
      //     onto a dark table header is grey-on-purple at 1.5:1 and is not.
      const onTop = order.get(el) > order.get(other);
      if (onTop && (ratio(textFill, fill) ?? AA) >= AA) continue;
      covered += hit;
      if (!calloutGroups.some(g => g.contains(other))) byLayout += hit;
      why = onTop ? 'unreadable on' : 'covered by';
    }
    if (covered > 1) {
      hiddenArea += covered;
      if (hidden.length < 8) {
        hidden.push({text: (el.textContent || '').trim().slice(0, 40), why,
                     covered: Math.round(covered),
                     fraction: +(covered / area(r)).toFixed(3)});
      }
    }
  });

  return {svg: rect(S), card: rect(cardBox), callouts, overflow: worst, offenders,
          hiddenText: Math.round(hiddenArea), hiddenByLayout: Math.round(byLayout), hidden};
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
  const {viewport = {width: 1200, height: 1000}, shadow = 8, weights = {}, jobs = [],
         shots = []} = job;

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
    // Rasterising is not measuring, and it is here anyway: it needs the same Chrome, and it
    // needs it for the same reason — a callout is HTML in a foreignObject, so only a real
    // browser lays it out. Still no decisions in JS; the scale and the size come from Python.
    for (const shot of shots) {
      await page.setViewport({width: Math.ceil(shot.width), height: Math.ceil(shot.height),
                             deviceScaleFactor: shot.scale || 2});
      await page.setContent(shot.html, {waitUntil: 'load'});
      await page.evaluate(() => document.fonts && document.fonts.ready);
      await page.screenshot({path: shot.out, omitBackground: false});
      results.push({key: shot.key, wrote: shot.out});
    }
    process.stdout.write(JSON.stringify({results}));
  } finally {
    await browser.close();
  }
})().catch(e => fail(`browser measurement failed: ${e && e.stack || e}`));
