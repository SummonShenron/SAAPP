import React, { useEffect, useMemo, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { BASE_URL } from '../api';
import { makeSeenWordsPlugin } from './streamFade';

// The text of one chat message as Markdown (pages/Chat.tsx). It is its own memoized component for two reasons.
//
// 1. SPEED. While a reply streams, the message list is re-rendered many times a second. The Markdown of EVERY message used to be re-parsed each time, because the `components` map below was a new object on every
//    render and so every ReactMarkdown saw "new props". Here the map is made once per message and the component only re-renders when its own text, or whether it is still streaming, changes: a long thread costs the
//    same to stream into as a short one.
// 2. THE FADE. While streaming, each word fades in once (components/streamFade.ts). The words already on screen are counted after every render and handed to the plugin on the next, so a word that is re-created
//    when the Markdown changes shape (a closing `**`, a list starting) does not fade in a second time.

interface MessageMarkdownProps {
  content: string;
  /** True while this reply's words are still arriving: its words fade in, once each. */
  streaming: boolean;
  messageId: string;
  /** Opens the memory a "Source: Memory" citation points at. Must be stable (a ref-backed callback), or every message re-renders when any one does. */
  onOpenMemory: (messageId: string) => void;
  /** The current sign-in token, for opening a document link (read when a link is clicked, never at render). */
  getTokenRef: React.MutableRefObject<(() => Promise<string | null>) | undefined>;
  /** The fenced code block renderer (it lives in pages/Chat.tsx). Must be a module-level component. */
  CodeBlock: React.ComponentType<{ language: string; code: string }>;
}

function MessageMarkdownImpl({ content, streaming, messageId, onOpenMemory, getTokenRef, CodeBlock }: MessageMarkdownProps) {
  // How many words were on screen after the last render, and how many the render in progress produced (the plugin writes it; the effect below commits it).
  const seenRef = useRef(0);
  const countedRef = useRef(0);
  useEffect(() => {
    seenRef.current = countedRef.current;
  });

  // The refs are read and written when ReactMarkdown RUNS the plugin (a moment after this render, in its own render), never while this component renders, which the lint rule cannot see.
  const rehypePlugins = useMemo(
    () => (streaming ? [makeSeenWordsPlugin({ seen: () => seenRef.current, counted: total => { countedRef.current = total; } })] : undefined),    // eslint-disable-line react-hooks/refs
    [streaming],
  );

  const components = useMemo(() => ({
    // A language-less fenced block (bare ```) gets no className at all —
    // identical to a true inline `code` span — so neither className nor
    // content (a single-line fenced block has no internal newline either)
    // reliably tells them apart from inside `code` itself. `pre`, though, is
    // structurally guaranteed by the CommonMark AST to appear only for a
    // fenced block and never for an inline span (verified directly), so it
    // does the block/inline split instead, reaching into the `code` element
    // it wraps (already rendered by the branch below) for the language + text.
    code: ({ children, ...rest }: React.ComponentPropsWithoutRef<'code'>) => (
      <code {...rest} className="reply-code">
        {children}
      </code>
    ),
    pre: ({ children }: { children?: React.ReactNode }) => {
      const codeEl = children as { props?: { className?: string; children?: React.ReactNode } };
      const match = /language-(\w+)/.exec(codeEl?.props?.className || '');
      const raw = String(codeEl?.props?.children ?? '').replace(/\n$/, '');
      return <CodeBlock language={match ? match[1] : 'text'} code={raw} />;
    },
    a: ({ href, children, ...props }: React.ComponentPropsWithoutRef<'a'> & { node?: unknown }) => {
      const rest: Record<string, unknown> = { ...props };
      delete rest.node;      // react-markdown passes the syntax-tree node; it is not an attribute of the link
      const finalHref = href?.startsWith('/')
        ? `${BASE_URL.replace(/\/$/, '')}${href}`
        : href;
      // The citation for the person's own memory is not a file: it opens the memory the answer drew on. (Replies saved before this carry the old download link.)
      const isMemoryLink = href === '#memory-source' || finalHref?.endsWith('/api/documents/download/user_memory');
      const isDownloadLink = !isMemoryLink && finalHref?.includes('/api/documents/download/');
      const normalizedLabel = React.Children.toArray(children)
        .map((child) => (typeof child === 'string' ? child : ''))
        .join(' ')
        .replace(/\s+/g, ' ')
        .trim();

      const handleClick = async (e: React.MouseEvent) => {
        if (isDownloadLink && finalHref) {
          e.preventDefault();
          const newTab = window.open('', '_blank');
          if (newTab) {
            newTab.document.write('<html><body style="background: #121824; color: #fff; font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;"><h3>Loading secure document preview...</h3></body></html>');
          }
          try {
            let token = null;
            const isGuest = ['guest', 'guest_bty'].includes(localStorage.getItem('principal') || '');
            if (isGuest) {
              token = localStorage.getItem('guest_token');
            } else if (getTokenRef.current) {
              token = await getTokenRef.current();
            }
            const response = await fetch(finalHref, {
              headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
            });
            if (!response.ok) throw new Error(`Server responded with status ${response.status}`);
            const blob = await response.blob();
            const blobUrl = window.URL.createObjectURL(blob);
            if (newTab) newTab.location.href = blobUrl;
            else window.open(blobUrl, '_blank');
          } catch (err) {
            console.error("Document preview failed:", err);
            if (newTab) newTab.close();
            alert("Failed to load document. Please check your session.");
          }
        }
      };

      if (isMemoryLink) {
        return (
          <button type="button" className="citation-link citation-link-document citation-link-memory" onClick={() => onOpenMemory(messageId)}>
            Source: Memory
          </button>
        );
      }

      return (
        <a
          {...rest}
          href={finalHref}
          onClick={handleClick}
          target="_blank"
          rel="noopener noreferrer"
          className={isDownloadLink ? 'citation-link citation-link-document' : 'citation-link'}
          style={{ cursor: 'pointer' }}
        >
          {normalizedLabel || children}
        </a>
      );
    },
  }), [messageId, onOpenMemory, getTokenRef, CodeBlock]);

  return (
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={rehypePlugins} components={components}>
      {content}
    </ReactMarkdown>
  );
}

const REMARK_PLUGINS = [remarkGfm];

export const MessageMarkdown = React.memo(MessageMarkdownImpl);
