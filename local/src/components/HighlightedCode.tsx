import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { CODE_BOX_STYLE } from './codeBoxStyle';

/**
 * The syntax-highlighted code itself. It lives in its own file because the highlighter carries a grammar for dozens of languages and is the
 * largest single piece of the page's JavaScript; the chat loads this file the first time a reply contains a code block, not before.
 */
export default function HighlightedCode({ language, code }: { language: string; code: string }) {
  return (
    <SyntaxHighlighter language={language || 'text'} style={vscDarkPlus} PreTag="div" customStyle={CODE_BOX_STYLE}>
      {code}
    </SyntaxHighlighter>
  );
}
