// Shared browser bootstrap for the gates/placement scripts.
//
// We use puppeteer-core (29MB, no bundled browser) driving the SYSTEM Chrome, rather than the
// full puppeteer package (which downloads its own ~550MB Chromium). During prototyping the
// browser came incidentally from a mermaid-cli install; that is gone.
//
// Override either piece with env vars if the defaults do not fit:
//   PUPPETEER_CORE=/path/to/puppeteer-core     PUPPETEER_EXECUTABLE_PATH=/path/to/chrome
const {execSync} = require('child_process');

const CANDIDATES = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
];

function corePath() {
  if (process.env.PUPPETEER_CORE) return process.env.PUPPETEER_CORE;
  const root = execSync('npm root -g', {encoding: 'utf8'}).trim();
  return `${root}/puppeteer-core`;
}

function chromePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) return process.env.PUPPETEER_EXECUTABLE_PATH;
  const fs = require('fs');
  const hit = CANDIDATES.find(p => fs.existsSync(p));
  if (!hit) {
    console.error('No system Chrome/Chromium/Edge found. Set PUPPETEER_EXECUTABLE_PATH, or\n' +
                  'install the full package instead: npm i -g puppeteer');
    process.exit(2);
  }
  return hit;
}

exports.launch = async () => {
  const puppeteer = require(corePath());
  return puppeteer.launch({headless: 'new', executablePath: chromePath()});
};
