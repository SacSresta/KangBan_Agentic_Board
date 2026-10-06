'use client';

import { useState, type FormEvent } from 'react';
import { api } from '../lib/api';
import type { Project, Status, Ticket } from '../lib/contracts';
import { Modal } from './modal';

export function TicketEditor({ project, ticket, tickets, status = 'Backlog', onClose, onSaved }: { project: Project; ticket?: Ticket; tickets: Ticket[]; status?: Status; onClose(): void; onSaved(): Promise<void> }) {
  const [title, setTitle] = useState(ticket?.title ?? '');
  const [description, setDescription] = useState(ticket?.description ?? '');
  const [criteria, setCriteria] = useState(ticket?.acceptance_criteria?.join('\n') ?? '');
  const [priority, setPriority] = useState(ticket?.priority ?? 3);
  const [dependencies, setDependencies] = useState<string[]>(ticket?.dependencies ?? []);
  const [initialStatus, setInitialStatus] = useState<Status>(ticket?.status ?? status);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  async function save(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError('');
    try {
      const values = { title: title.trim(), description, acceptance_criteria: criteria.split('\n').map(item => item.trim()).filter(Boolean), priority, dependencies };
      if (ticket) await api.patchTicket(ticket.id, values);
      else await api.createTicket({ ...values, project_id: project.id, status: initialStatus, position: Math.max(0, ...tickets.map(item => item.position ?? 0)) + 1 });
      await onSaved(); onClose();
    } catch (error) { setError(error instanceof Error ? error.message : 'Could not save ticket'); }
    finally { setSaving(false); }
  }
  return <Modal title={ticket ? 'Edit ticket' : 'Create a ticket'} eyebrow={project.name} onClose={onClose}><form className="modal-body form-stack" onSubmit={save}>
    <label>Title<input autoFocus required maxLength={300} value={title} onChange={event => setTitle(event.target.value)} placeholder="What should the agents build?" /></label>
    <label>Description<textarea rows={5} value={description} onChange={event => setDescription(event.target.value)} placeholder="Give the engineer context and define the scope." /></label>
    <label>Acceptance criteria<textarea rows={4} value={criteria} onChange={event => setCriteria(event.target.value)} placeholder="One clear, testable requirement per line" /><span className="field-hint">One criterion per line. The frontier reviewer checks each one.</span></label>
    <div className="form-grid"><label>Priority<select value={priority} onChange={event => setPriority(Number(event.target.value))}><option value={1}>P1 · Urgent</option><option value={2}>P2 · High</option><option value={3}>P3 · Normal</option><option value={4}>P4 · Low</option><option value={5}>P5 · Later</option></select></label>{!ticket && <label>Initial status<select value={initialStatus} onChange={event => setInitialStatus(event.target.value as Status)}><option>Backlog</option><option>Ready</option></select></label>}</div>
    <fieldset className="dependency-field"><legend>Dependencies</legend>{tickets.filter(item => item.id !== ticket?.id && item.project_id === project.id).length ? <div className="dependency-options">{tickets.filter(item => item.id !== ticket?.id && item.project_id === project.id).map(item => <label className="checkbox-label" key={item.id}><input type="checkbox" checked={dependencies.includes(item.id)} onChange={event => setDependencies(event.target.checked ? [...dependencies, item.id] : dependencies.filter(id => id !== item.id))} /><span>{item.title}<small>{item.status}</small></span></label>)}</div> : <p className="muted">No other tickets in this project.</p>}<span className="field-hint">Dependencies must be Done before this ticket can run.</span></fieldset>
    {error && <p role="alert" className="form-error">{error}</p>}<footer className="form-actions"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button className="button primary" disabled={saving}>{saving ? 'Saving…' : ticket ? 'Save ticket' : 'Create ticket'}</button></footer>
  </form></Modal>;
}
