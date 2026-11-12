// A file's icon by its type, as an editor's file tree draws one (neovim's devicons, lsd,
// yazi): the Nerd Fonts Seti glyph and a colour of the language's own, by the file's name.
// Code points from the Nerd Fonts name list (v3.5.1), by the names given.

export type FileIcon = { glyph: string; color?: string }

const BY_EXT: Record<string, FileIcon> = {
  ts: { glyph: '\u{E628}', color: 'blue' }, // seti-typescript
  mts: { glyph: '\u{E628}', color: 'blue' },
  cts: { glyph: '\u{E628}', color: 'blue' },
  tsx: { glyph: '\u{E625}', color: 'cyan' }, // seti-react
  jsx: { glyph: '\u{E625}', color: 'cyan' },
  js: { glyph: '\u{E60C}', color: 'yellow' }, // seti-javascript
  mjs: { glyph: '\u{E60C}', color: 'yellow' },
  cjs: { glyph: '\u{E60C}', color: 'yellow' },
  py: { glyph: '\u{E606}', color: 'yellow' }, // seti-python
  json: { glyph: '\u{E60B}', color: 'yellow' }, // seti-json
  md: { glyph: '\u{E609}', color: 'white' }, // seti-markdown
  yml: { glyph: '\u{E6A8}', color: 'magenta' }, // seti-yml
  yaml: { glyph: '\u{E6A8}', color: 'magenta' },
  sh: { glyph: '\u{E691}', color: 'green' }, // seti-shell
  bash: { glyph: '\u{E691}', color: 'green' },
  zsh: { glyph: '\u{E691}', color: 'green' },
  fish: { glyph: '\u{E691}', color: 'green' },
  css: { glyph: '\u{E614}', color: 'blue' }, // seti-css
  scss: { glyph: '\u{E603}', color: 'magenta' }, // seti-sass
  sass: { glyph: '\u{E603}', color: 'magenta' },
  html: { glyph: '\u{E60E}', color: 'red' }, // seti-html
  xml: { glyph: '\u{E619}', color: 'red' }, // seti-xml
  vue: { glyph: '\u{E6A0}', color: 'green' }, // seti-vue
  svelte: { glyph: '\u{E697}', color: 'red' }, // seti-svelte
  go: { glyph: '\u{E627}', color: 'cyan' }, // seti-go
  rs: { glyph: '\u{E68B}', color: 'red' }, // seti-rust
  java: { glyph: '\u{E66D}', color: 'red' }, // seti-java
  toml: { glyph: '\u{E615}', color: 'gray' }, // seti-config
  ini: { glyph: '\u{E615}', color: 'gray' },
  cfg: { glyph: '\u{E615}', color: 'gray' },
  lock: { glyph: '\u{E672}', color: 'gray' }, // seti-lock
}

const BY_NAME: Record<string, FileIcon> = {
  dockerfile: { glyph: '\u{E650}', color: 'blue' }, // seti-docker
}

// Any other file: a plain page (md-file_outline).
const PLAIN: FileIcon = { glyph: '\u{F0224}' }

export const iconOfFile = (path: string): FileIcon => {
  const name = (path.split('/').pop() ?? '').toLowerCase()
  const byName = BY_NAME[name]
  if (byName) return byName
  const dot = name.lastIndexOf('.')
  return (dot > 0 ? BY_EXT[name.slice(dot + 1)] : undefined) ?? PLAIN
}

// The mark after the name of the file a comment is on (md-comment_text_outline).
export const COMMENTED = '\u{F0189}'
