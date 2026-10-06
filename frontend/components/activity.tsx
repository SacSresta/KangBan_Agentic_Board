'use client';

// ToolResult JSON parsing, value tables and expansion adapted from Agent Chat UI (MIT).
// See third_party/agent-chat-ui.LICENSE. Our messages come from FastAPI Activity events.
import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Bot, ChevronDown, ChevronUp, ClipboardList, FlaskConical, ShieldCheck, Terminal, UserRound } from 'lucide-react';
import type { Activity } from '../lib/contracts';

function isComplexValue(value: unknown): value is Record<string, unknown> | unknown[] {
  return Array.isArray(value) || (typeof value === 'object' && value !== null);
}

export function ToolResult({ content, name }: { content: string; name: string }) {
  const [isExpanded, setIsExpanded] = useState(false);
  let parsedContent: unknown;
  try { parsedContent = JSON.parse(content); } catch { parsedContent = content; }
  const isJsonContent = isComplexValue(parsedContent);
  const contentStr = isJsonContent ? JSON.stringify(parsedContent, null, 2) : content;
  const contentLines = contentStr.split('\n');
  const shouldTruncate = contentLines.length > 4 || contentStr.length > 500;
  const displayedContent = shouldTruncate && !isExpanded ? (contentStr.length > 500 ? contentStr.slice(0, 500) + '…' : contentLines.slice(0, 4).join('\n') + '\n…') : contentStr;
  const entries = isComplexValue(parsedContent) ? (Array.isArray(parsedContent) ? parsedContent.map((value, key) => [String(key), value] as const) : Object.entries(parsedContent)) : [];
  return <div className="tool-result"><div className="tool-heading"><Terminal size={14} /><strong>{name.replaceAll('_', ' ')}</strong></div>
    {isJsonContent ? <table className="tool-table"><tbody>{(isExpanded ? entries : entries.slice(0, 5)).map(([key, value]) => <tr key={key}><th>{key}</th><td>{isComplexValue(value) ? <pre>{JSON.stringify(value, null, 2)}</pre> : String(value)}</td></tr>)}</tbody></table> : <pre className="tool-content">{displayedContent}</pre>}
    {(shouldTruncate || entries.length > 5) && <button className="expand-result" onClick={() => setIsExpanded(!isExpanded)} aria-expanded={isExpanded}>{isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}{isExpanded ? 'Show less' : 'Show full output'}</button>}
  </div>;
}

export function ActivityFeed({ activities }: { activities: Activity[] }) {
  if (!activities.length) return <div className="detail-empty"><Bot size={26} /><p>Agent activity will appear here when execution starts.</p></div>;
  return <div className="activity-feed">{activities.map(event => {
    const Icon = event.role.toLowerCase().includes('test') ? FlaskConical : event.role.toLowerCase().includes('frontier') ? ShieldCheck : event.role.toLowerCase().includes('scrum') ? ClipboardList : event.role.toLowerCase().includes('human') ? UserRound : Bot;
    const tool = /tool|test_result|test_output/.test(event.event_type);
    return <article className="activity-message" key={event.id}><div className={`agent-avatar ${event.role.toLowerCase().includes('frontier') ? 'review-avatar' : ''}`}><Icon size={17} /></div><div className="activity-body"><div className="activity-meta"><strong>{event.role}</strong><span>{event.event_type.replaceAll('_', ' ')}</span><time dateTime={event.timestamp}>{new Date(event.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time></div>{tool ? <ToolResult content={event.message} name={event.event_type} /> : <div className="markdown"><ReactMarkdown>{event.message}</ReactMarkdown></div>}</div></article>;
  })}</div>;
}
