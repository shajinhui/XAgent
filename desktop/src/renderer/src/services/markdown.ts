import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import cpp from 'highlight.js/lib/languages/cpp'
import css from 'highlight.js/lib/languages/css'
import go from 'highlight.js/lib/languages/go'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import MarkdownIt from 'markdown-it'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'

const markdown = new MarkdownIt({
  breaks: true,
  html: false,
  linkify: true,
  typographer: false
})

hljs.registerLanguage('bash', bash)
hljs.registerLanguage('cpp', cpp)
hljs.registerLanguage('css', css)
hljs.registerLanguage('go', go)
hljs.registerLanguage('java', java)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('python', python)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('yaml', yaml)

const languageAliases: Record<string, string> = {
  c: 'cpp',
  cplusplus: 'cpp',
  html: 'xml',
  js: 'javascript',
  py: 'python',
  sh: 'bash',
  shell: 'bash',
  ts: 'typescript',
  vue: 'xml',
  yml: 'yaml',
  zsh: 'bash'
}

const defaultTableOpenRenderer =
  markdown.renderer.rules.table_open ||
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))

const defaultTableCloseRenderer =
  markdown.renderer.rules.table_close ||
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))

function normalizeLanguage(language: string): string {
  const key = language.toLowerCase()
  return languageAliases[key] || key
}

function highlightCode(content: string, language: string): string {
  const normalizedLanguage = normalizeLanguage(language)
  if (!normalizedLanguage || ['text', 'txt', 'plain', 'plaintext'].includes(normalizedLanguage)) {
    return markdown.utils.escapeHtml(content)
  }

  if (!hljs.getLanguage(normalizedLanguage)) {
    return markdown.utils.escapeHtml(content)
  }

  try {
    return hljs.highlight(content, {
      language: normalizedLanguage,
      ignoreIllegals: true
    }).value
  } catch {
    return markdown.utils.escapeHtml(content)
  }
}

markdown.renderer.rules.fence = (tokens, idx) => {
  const token = tokens[idx]
  const info = token.info ? markdown.utils.unescapeAll(token.info).trim() : ''
  const language = info ? info.split(/\s+/g)[0] : ''
  const languageLabel = language || 'text'
  const content = token.content.replace(/^\s*\n+/, '').replace(/\n+\s*$/, '')
  const languageClass = language
    ? ` class="hljs language-${markdown.utils.escapeHtml(language)}"`
    : ' class="hljs language-text"'
  const highlightedContent = highlightCode(content, language)

  return [
    '<div class="code-block">',
    `<span class="code-language">${markdown.utils.escapeHtml(languageLabel)}</span>`,
    '<div class="code-block-actions">',
    '<button class="code-copy-button" type="button" data-copy-code="true" aria-label="复制代码" title="复制代码">复制</button>',
    '</div>',
    `<pre><code${languageClass}>${highlightedContent}</code></pre>`,
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
