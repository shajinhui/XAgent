import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'

const markdown = new MarkdownIt({
  breaks: true,
  html: false,
  linkify: true,
  typographer: false
})

const defaultTableOpenRenderer =
  markdown.renderer.rules.table_open ||
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))

const defaultTableCloseRenderer =
  markdown.renderer.rules.table_close ||
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))

markdown.renderer.rules.fence = (tokens, idx) => {
  const token = tokens[idx]
  const info = token.info ? markdown.utils.unescapeAll(token.info).trim() : ''
  const language = info ? info.split(/\s+/g)[0] : ''
  const languageLabel = language
    ? `<span class="code-language">${markdown.utils.escapeHtml(language)}</span>`
    : ''
  const content = token.content.replace(/^\s*\n+/, '').replace(/\n+\s*$/, '')
  const languageClass = language ? ` class="language-${markdown.utils.escapeHtml(language)}"` : ''

  return [
    '<div class="code-block">',
    '<div class="code-block-actions">',
    languageLabel,
    '<button class="code-copy-button" type="button" data-copy-code="true" aria-label="复制代码" title="复制代码">复制</button>',
    '</div>',
    `<pre><code${languageClass}>${markdown.utils.escapeHtml(content)}</code></pre>`,
    '</div>'
  ].join('')
}

markdown.renderer.rules.table_open = (tokens, idx, options, env, self) => {
  return `<div class="markdown-table-wrap">${defaultTableOpenRenderer(tokens, idx, options, env, self)}`
}

markdown.renderer.rules.table_close = (tokens, idx, options, env, self) => {
  return `${defaultTableCloseRenderer(tokens, idx, options, env, self)}</div>`
}

export function renderMarkdown(source: string): string {
  return DOMPurify.sanitize(markdown.render(source))
}
