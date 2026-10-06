'use client';

import { useEffect, useRef, useState } from 'react';
import { Check, CheckCircle2, GitBranch, ListChecks, Pencil, Play, ShieldCheck } from 'lucide-react';
import { api } from '../lib/api';
import type { Activity, Artifact, Project, Review, Run, Ticket } from '../lib/contracts';
import { ActivityFeed, ToolResult } from './activity';
import { Modal } from './modal';

export function TicketDetail({ ticket, project, tickets, onClose, onEdit, onChanged }: { ticket: Ticket; project: Project; tickets: Ticket[]; onClose(): void; onEdit(): void; onChanged(): Promise<void> }) {
  const [run, setRun] = useState<Run | null>(null);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [tab, setTab] = useState('Activity');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [inspected, setInspected] = useState(false);
  const cursor = useRef(0);
  const lastRunId = useRef('');
  const lastArtifact = useRef('');

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const history = await api.runs(ticket.id);
        const latest = [...history].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
        if (!latest || cancelled) return;
        const changedRun = latest.id !== lastRunId.current;
        if (changedRun) { cursor.current = 0; lastRunId.current = latest.id; lastArtifact.current = ''; setActivities([]); setArtifact(null); }
        const [events, reviewList] = await Promise.all([api.activity(latest.id, cursor.current), api.reviews(latest.id)]);
        if (cancelled) return;
        setRun(latest); setReviews(reviewList);
        if (events.length) {
          cursor.current = Math.max(cursor.current, ...events.map(event => event.id));
          setActivities(previous => [...previous, ...events.filter(event => !previous.some(old => old.id === event.id))]);
        }
        if (latest.artifact_commit && latest.artifact_commit !== lastArtifact.current) {
          const result = await api.artifact(latest.id);
          if (!cancelled) { setArtifact(result); lastArtifact.current = latest.artifact_commit; }
        } else if (!latest.artifact_commit) { setArtifact(null); lastArtifact.current = ''; }
      } catch (error) { if (!cancelled) setError(error instanceof Error ? error.message : 'Could not load run'); }
      finally { if (!cancelled) timer = setTimeout(poll, 1400); }
    }
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [ticket.id]);

  async function action(action: 'approve' | 'changes' | 'confirm-merge' | 'resume' | 'ready') {
    setBusy(true); setError('');
    try {
      if (action === 'ready') await api.patchTicket(ticket.id, { status: 'Ready' });
      else if (run) {
        if (action === 'resume') setRun(await api.resume(run.id));
        else if (run.artifact_commit) setRun(await api.decide(run.id, action, run.artifact_commit, message));
        else throw new Error('No reviewable artifact is available.');
      }
      setInspected(false); await onChanged();
    } catch (error) { setError(error instanceof Error ? error.message : 'Action failed'); }
    finally { setBusy(false); }
  }
  const currentReview = [...reviews].reverse().find(review => review.artifact_commit === run?.artifact_commit);
  const eligibleHuman = run?.outcome === 'awaiting_human' && currentReview?.verdict === 'approve';
  const testEvents = activities.filter(event => event.role.toLowerCase().includes('test') || /test/.test(event.event_type));
  const currentTicket = tickets.find(item => item.id === ticket.id) ?? ticket;
  return <Modal title={currentTicket.title} eyebrow={`KB-${ticket.id.slice(0, 5).toUpperCase()} · ${project.name}`} onClose={onClose} wide><div className="modal-body ticket-detail">
    <div className="detail-toolbar"><span className="status-chip">{currentTicket.status}</span><span className={`priority priority-${ticket.priority ?? 3}`}>Priority {ticket.priority ?? 3}</span><button className="button secondary small" onClick={onEdit} disabled={!['Backlog', 'Ready'].includes(currentTicket.status ?? 'Backlog')} title={['Backlog', 'Ready'].includes(currentTicket.status ?? 'Backlog') ? 'Edit ticket details' : 'Request changes to revise a reviewed artifact.'}><Pencil size={14} />Edit ticket</button></div>
    <p className="ticket-description">{currentTicket.description || 'No description provided.'}</p>
    <div className="criteria-section"><h3><ListChecks size={16} />Acceptance criteria</h3>{currentTicket.acceptance_criteria?.length ? <ul>{currentTicket.acceptance_criteria.map((criterion, index) => <li key={index}><span className="criterion-bullet" />{criterion}</li>)}</ul> : <p className="muted">No acceptance criteria defined.</p>}</div>
    {!!currentTicket.dependencies?.length && <div className="detail-dependencies"><GitBranch size={14} /><strong>Depends on</strong>{currentTicket.dependencies.map(id => <span key={id}>{tickets.find(item => item.id === id)?.title ?? id}</span>)}</div>}
    {run && <div className="run-strip"><div><span>Phase</span><strong>{run.phase}</strong></div><div><span>Repair rounds</span><strong>{run.attempts} / 2</strong></div><div><span>Tool calls</span><strong>{run.tool_calls} / 20</strong></div><div><span>Artifact</span><code>{run.artifact_commit?.slice(0, 10) ?? 'In preparation'}</code></div></div>}
    <div className="tabs" role="tablist" aria-label="Ticket evidence">{['Activity', 'Test output', 'Patch', 'Frontier review'].map(item => <button key={item} role="tab" aria-selected={tab === item} onClick={() => setTab(item)} className={tab === item ? 'active' : ''}>{item}{item === 'Activity' && activities.length > 0 && <span>{activities.length}</span>}</button>)}</div>
    <div className="tab-panel" role="tabpanel" aria-label={tab}>
      {tab === 'Activity' && <ActivityFeed activities={activities} />}
      {tab === 'Test output' && (testEvents.length ? <div className="test-results">{testEvents.map(event => <ToolResult key={event.id} content={event.message} name={`${event.role} · ${event.event_type}`} />)}</div> : <p className="muted">Captured test commands and results will appear here.</p>)}
      {tab === 'Patch' && (artifact ? <><div className="artifact-meta"><p><strong>Branch</strong><code>{artifact.branch}</code></p><p><strong>Worktree</strong><code>{artifact.worktree}</code></p><p><strong>Commit</strong><code>{artifact.artifact_commit}</code></p></div><pre className="patch-output">{artifact.patch || 'No file changes in this artifact.'}</pre></> : <p className="muted">The patch becomes available when an artifact commit is created.</p>)}
      {tab === 'Frontier review' && (reviews.length ? <div className="review-list">{[...reviews].reverse().map(review => <article className={`review-card ${review.artifact_commit === run?.artifact_commit ? '' : 'stale-review'}`} key={review.id}><header><ShieldCheck size={18} /><strong>{review.verdict === 'approve' ? 'Frontier approved' : review.verdict === 'changes' ? 'Changes requested' : 'Frontier unavailable'}</strong><code>{review.artifact_commit.slice(0, 10)}</code></header>{review.artifact_commit !== run?.artifact_commit && <p className="muted">Historical review · does not apply to the current artifact</p>}<ul>{review.findings?.map((finding, index) => <li key={index}>{finding}</li>)}</ul>{Object.entries(review.acceptance_assessment ?? {}).map(([criterion, assessment]) => <div className="review-criterion" key={criterion}><Check size={14} /><div><strong>{criterion}</strong><p>{assessment}</p></div></div>)}</article>)}</div> : <p className="muted">A frontier reviewer must inspect the exact artifact before human approval.</p>)}
    </div>
    {run?.outcome === 'blocked' && <div className="decision-panel blocked-panel"><h3>Inspect before resuming</h3><p>{run.interruption_reason || 'This run needs attention. Inspect its worktree, activity, and unfinished tool operations before resuming.'}</p>{run.worktree && <code>{run.worktree}</code>}<label className="checkbox-label"><input type="checkbox" checked={inspected} onChange={event => setInspected(event.target.checked)} /><span>I inspected the retained worktree and understand that resume invalidates earlier approvals.</span></label><button className="button primary" disabled={busy || !inspected} onClick={() => action('resume')}><Play size={15} />Resume blocked work</button></div>}
    {eligibleHuman && <div className="decision-panel"><h3>Your review is next</h3><p>Approval records commit <code>{run?.artifact_commit?.slice(0, 10)}</code>. Merge the branch manually after approving.</p><label>Review feedback<textarea rows={2} value={message} onChange={event => setMessage(event.target.value)} placeholder="Describe requested changes or add an approval note" /></label><div className="decision-actions"><button className="button secondary" disabled={busy || !message.trim()} onClick={() => action('changes')}>Request changes</button><button className="button primary" disabled={busy} onClick={() => action('approve')}><CheckCircle2 size={15} />Approve artifact</button></div></div>}
    {run?.outcome === 'approved' && <div className="decision-panel"><h3>Approved · awaiting your manual merge</h3><p>Merge branch <code>{run.branch}</code> into <code>{project.base_branch}</code>. Confirmation verifies that the approved commit is contained in the base branch.</p><label>Review feedback<textarea rows={2} value={message} onChange={event => setMessage(event.target.value)} placeholder="Describe any further changes needed before merging" /></label><div className="decision-actions"><button className="button secondary" disabled={busy || !message.trim()} onClick={() => action('changes')}>Request changes</button><button className="button primary" disabled={busy} onClick={() => action('confirm-merge')}><CheckCircle2 size={15} />Confirm manual merge</button></div></div>}
    {currentTicket.status === 'Backlog' && <div className="detail-footer"><button className="button primary" disabled={busy} onClick={() => action('ready')}><Play size={15} />Move to Ready</button></div>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </div></Modal>;
}
