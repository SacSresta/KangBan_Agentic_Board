'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, Bot, CircleHelp, FolderGit2, GitBranch, Layers3, LayoutDashboard, Moon, Pause, Play, Plus, Search, Settings2, ShieldCheck, Sun, X } from 'lucide-react';
import { api } from '../lib/api';
import type { Project, QueueState, Status, Ticket, TicketPatch } from '../lib/contracts';
import { Board } from '../components/board';
import { SettingsPanel } from '../components/settings';
import { TicketEditor } from '../components/ticket-editor';
import { TicketDetail } from '../components/ticket-detail';

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState('');
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [queue, setQueue] = useState<QueueState>({ enabled: false, active_run_id: null });
  const [mode, setMode] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [newProject, setNewProject] = useState(false);
  const [editor, setEditor] = useState<{ ticket?: Ticket; status: Status } | null>(null);
  const [detail, setDetail] = useState<Ticket | null>(null);
  const [search, setSearch] = useState('');
  const [view, setView] = useState<'board' | 'review'>('board');
  const [dark, setDark] = useState(false);
  const refreshSequence = useRef(0);
  const project = projects.find(item => item.id === projectId);

  useEffect(() => {
    const saved = localStorage.getItem('kanban-project');
    if (saved) setProjectId(saved);
    const theme = localStorage.getItem('kanban-theme') ?? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    setDark(theme === 'dark');
  }, []);
  useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light'; }, [dark]);

  const refresh = useCallback(async (preferredId?: string) => {
    const sequence = ++refreshSequence.current;
    const [projectList, queueState, health] = await Promise.all([api.projects(), api.queue(), api.health()]);
    const selected = projectList.find(item => item.id === (preferredId ?? projectId))?.id ?? projectList[0]?.id ?? '';
    const ticketList = selected ? await api.tickets(selected) : [];
    if (sequence !== refreshSequence.current) return;
    setProjects(projectList); setQueue(queueState); setMode(health.mode);
    if (selected !== projectId) setProjectId(selected);
    setTickets(ticketList);
    setLoading(false);
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { await refresh(); if (!cancelled) setError(''); }
      catch (error) { if (!cancelled) { setError(error instanceof Error ? error.message : 'Could not load board'); setLoading(false); } }
      finally { if (!cancelled) timer = setTimeout(poll, 2000); }
    }
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [refresh]);

  const filtered = useMemo(() => tickets.filter(ticket => (!search || `${ticket.title} ${ticket.description} ${ticket.id}`.toLowerCase().includes(search.toLowerCase())) && (view !== 'review' || ['Review', 'Awaiting Human', 'Approved', 'Blocked'].includes(ticket.status ?? 'Backlog'))), [tickets, search, view]);
  const ready = tickets.filter(ticket => ticket.status === 'Ready').length;
  const waiting = tickets.filter(ticket => ticket.status === 'Awaiting Human').length;
  const done = tickets.filter(ticket => ticket.status === 'Done').length;
  function selectProject(id: string) { setProjectId(id); localStorage.setItem('kanban-project', id); setDetail(null); }
  async function move(ticket: Ticket, patch: TicketPatch) {
    try { await api.patchTicket(ticket.id, patch); await refresh(); }
    catch (error) { setError(error instanceof Error ? error.message : 'Could not move ticket'); }
  }
  async function toggleQueue() {
    setBusy(true); setError('');
    try { setQueue(queue.enabled ? await api.pause() : await api.start()); await refresh(); }
    catch (error) { setError(error instanceof Error ? error.message : 'Queue action failed'); }
    finally { setBusy(false); }
  }
  function openSettings(create = false) { setNewProject(create); setSettingsOpen(true); }

  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><div className="brand-mark"><Layers3 size={22} /></div><div><strong>Agent Board</strong><span>LOCAL WORKSPACE</span></div></div>
      <div className="workspace-picker"><span className="eyebrow">Workspace</span><div className="project-select"><FolderGit2 size={17} /><select aria-label="Current project" value={projectId} onChange={event => selectProject(event.target.value)}>{!projects.length && <option value="">No repository yet</option>}{projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div><button className="text-button" onClick={() => openSettings(true)}><Plus size={13} />Add repository</button></div>
      <nav aria-label="Workspace navigation"><button className={`nav-item ${view === 'board' ? 'selected' : ''}`} onClick={() => setView('board')}><LayoutDashboard size={17} /><span>Board</span><span className="nav-count">{tickets.length}</span></button><button className={`nav-item ${view === 'review' ? 'selected' : ''}`} onClick={() => setView('review')}><ShieldCheck size={17} /><span>Review inbox</span>{waiting > 0 && <span className="nav-count accent-count">{waiting}</span>}</button><button className="nav-item" onClick={() => openSettings()}><Settings2 size={17} /><span>Settings</span></button></nav>
      <div className="sidebar-note"><div className="note-icon"><Bot size={20} /></div><h3>One ticket. A whole team.</h3><p>Scrum, Engineer, Tester, and Frontier Reviewer. You make the final call.</p><div className="role-dots"><span>S</span><span>E</span><span>T</span><span>F</span><span className="human-dot">You</span></div></div>
      <div className="sidebar-bottom"><div className="local-status"><span className={`status-dot ${mode ? 'green' : 'neutral'}`} /><span>{mode ? 'Local service connected' : 'Connecting to local service'}</span></div><button className="theme-button" onClick={() => { const next = !dark; setDark(next); localStorage.setItem('kanban-theme', next ? 'dark' : 'light'); }} aria-label={dark ? 'Use light theme' : 'Use dark theme'}>{dark ? <Sun size={16} /> : <Moon size={16} />}<span>{dark ? 'Light appearance' : 'Dark appearance'}</span></button></div>
    </aside>
    <main className="main-content"><header className="topbar"><div className="breadcrumb"><FolderGit2 size={16} /><span>{project?.name ?? 'Your workspace'}</span><span className="breadcrumb-divider">/</span><strong>{view === 'board' ? 'Board' : 'Review inbox'}</strong></div><div className="mobile-workspace"><select aria-label="Current project on mobile" value={projectId} onChange={event => selectProject(event.target.value)}>{!projects.length && <option value="">Your workspace</option>}{projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button className="icon-button" aria-label="Add repository on mobile" onClick={() => openSettings(true)}><Plus size={16} /></button></div><div className="topbar-right">{project && <span className="branch-label"><GitBranch size={13} />{project.base_branch}</span>}<span className="local-badge">Local</span></div></header>
      <section className="board-intro"><div className="intro-copy"><span className="eyebrow">Your coding workspace</span><h1>{view === 'board' ? 'From idea to reviewed code.' : 'The final call is yours.'}</h1><p>{view === 'board' ? 'Set the priorities. Let your agents build. Review every change.' : 'Inspect the evidence, request changes, and approve the exact artifact.'}</p></div><button className="button primary new-ticket" onClick={() => project ? setEditor({ status: 'Backlog' }) : openSettings(true)}><Plus size={17} />New ticket</button></section>
      {mode && mode !== 'live' && <div className="mode-banner" role="status"><FlaskIcon /><strong>{mode === 'mixed' ? 'Mixed provider mode' : 'Deterministic test mode'}</strong><span>{mode === 'mixed' ? 'At least one role uses a scripted test provider.' : 'Scripted agents exercise real repository tools. Live model verification is not enabled.'}</span><button onClick={() => openSettings()}>Configure models<ArrowRight size={13} /></button></div>}
      <section className="board-toolbar"><div className="board-stats"><span><span className="status-dot blue" /><strong>{ready}</strong> ready</span><span><span className="status-dot pink" /><strong>{waiting}</strong> awaiting you</span><span><span className="status-dot teal" /><strong>{done}</strong> done</span></div><div className="board-actions"><label className="search-box"><Search size={15} /><input aria-label="Search tickets" placeholder="Search tickets…" value={search} onChange={event => setSearch(event.target.value)} />{search && <button aria-label="Clear search" onClick={() => setSearch('')}><X size={13} /></button>}</label><button className={`button ${queue.enabled ? 'secondary' : 'dark-button'}`} disabled={busy || !project} onClick={toggleQueue} title={queue.enabled ? 'Pause new claims; the active ticket reaches a safe checkpoint.' : 'Run eligible Ready tickets in priority order, one at a time.'}>{queue.enabled ? <Pause size={15} /> : <Play size={15} />}{queue.enabled ? 'Pause queue' : 'Start queue'}</button></div></section>
      <div className="queue-caption"><span className={`status-dot ${queue.active_run_id ? 'violet' : queue.enabled ? 'green' : 'neutral'}`} />{queue.active_run_id ? 'One ticket is executing' : queue.enabled ? 'Queue is running · waiting for eligible tickets' : 'Queue paused'}<span>Priority first · dependencies respected · manual merge</span></div>
      {error && <div className="error-banner" role="alert"><span>{error}</span><button className="text-button" onClick={() => refresh().then(() => setError('')).catch(error => setError(error.message))}>Retry</button></div>}
      {loading ? <div className="loading-board"><div className="loading-spinner" /><p>Opening your workspace…</p></div> : !project ? <section className="onboarding"><div className="onboarding-icon"><FolderGit2 size={32} /></div><span className="eyebrow">Start with a repository</span><h2>A workspace for your next idea.</h2><p>Connect a local Git repository, define your test commands, and create your first ticket.</p><button className="button primary" onClick={() => openSettings(true)}><FolderGit2 size={16} />Connect repository</button><div className="onboarding-footer"><ShieldCheck size={15} />Changes stay in dedicated worktrees until you review and merge.</div></section> : <Board tickets={filtered} onOpen={setDetail} onCreate={status => setEditor({ status })} onMove={move} />}
      <footer className="board-footer"><span><CircleHelp size={13} />Drag tickets between Backlog and Ready. Review states follow the workflow.</span><span>8 phases · one execution slot</span></footer>
    </main>
    {settingsOpen && <SettingsPanel project={newProject ? undefined : project} onClose={() => setSettingsOpen(false)} onSaved={async (saved) => { if (saved) { setNewProject(false); selectProject(saved.id); setProjects(previous => [...previous.filter(item => item.id !== saved.id), saved]); } await refresh(saved?.id); }} />}
    {editor && project && <TicketEditor project={project} ticket={editor.ticket} status={editor.status} tickets={tickets} onClose={() => setEditor(null)} onSaved={refresh} />}
    {detail && project && <TicketDetail key={detail.id} ticket={detail} project={project} tickets={tickets} onClose={() => setDetail(null)} onChanged={refresh} onEdit={() => { setEditor({ ticket: tickets.find(ticket => ticket.id === detail.id) ?? detail, status: detail.status ?? 'Backlog' }); setDetail(null); }} />}
  </div>;
}

function FlaskIcon() { return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M9 3h6M10 3v6l-6 10a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2L14 9V3M7 15h10" /></svg>; }
