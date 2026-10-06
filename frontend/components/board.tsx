'use client';

// Adapted from George Griffiths' MIT Kanban example. See third_party/kanban-board.LICENSE.
import { useState } from 'react';
import { DndContext, DragOverlay, KeyboardSensor, PointerSensor, useDroppable, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core';
import { SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { CheckCheck, GitBranch, GripVertical, ListChecks, Plus } from 'lucide-react';
import type { Status, Ticket, TicketPatch } from '../lib/contracts';

export const columns: { status: Status; color: string; hint: string }[] = [
  { status: 'Backlog', color: 'neutral', hint: 'Shape the next idea' },
  { status: 'Ready', color: 'blue', hint: 'Prioritized for the queue' },
  { status: 'In Progress', color: 'violet', hint: 'Agents are building' },
  { status: 'Review', color: 'amber', hint: 'Tests and frontier review' },
  { status: 'Awaiting Human', color: 'pink', hint: 'Your review is needed' },
  { status: 'Approved', color: 'green', hint: 'Ready for manual merge' },
  { status: 'Blocked', color: 'red', hint: 'Needs your attention' },
  { status: 'Done', color: 'teal', hint: 'Merged and verified' },
];

const humanState = (status: Status) => status === 'Backlog' || status === 'Ready';
export function sortTickets(tickets: Ticket[]) {
  return [...tickets].sort((a, b) => (a.position ?? 0) - (b.position ?? 0) || a.created_at.localeCompare(b.created_at));
}

function TicketContent({ ticket, onOpen }: { ticket: Ticket; onOpen?: () => void }) {
  return <>
    <div className="card-top"><span className="ticket-id">KB-{ticket.id.slice(0, 5).toUpperCase()}</span><span className={`priority priority-${ticket.priority ?? 3}`}>P{ticket.priority ?? 3}</span></div>
    <button className="card-title" onClick={onOpen} tabIndex={onOpen ? 0 : -1}>{ticket.title}</button>
    {ticket.description && <p className="card-description">{ticket.description.replace(/```[\s\S]*?```/g, '').trim() || 'Scripted implementation fixture'}</p>}
    <div className="card-footer"><span><ListChecks size={13} />{ticket.acceptance_criteria?.length ?? 0} criteria</span>{!!ticket.dependencies?.length && <span title="Dependencies"><GitBranch size={13} />{ticket.dependencies.length}</span>}{ticket.status === 'Done' && <CheckCheck size={14} />}</div>
  </>;
}

function TicketCard({ ticket, onOpen }: { ticket: Ticket; onOpen(): void }) {
  const draggable = humanState(ticket.status ?? 'Backlog');
  const { setNodeRef, attributes, listeners, transform, transition, isDragging } = useSortable({
    id: ticket.id, disabled: !draggable,
    data: { type: 'Task', ticket },
    attributes: { roleDescription: 'Ticket' },
  });
  return <article ref={setNodeRef} style={{ transition, transform: CSS.Translate.toString(transform) }} className={`ticket-card ${isDragging ? 'dragging' : ''}`} data-testid={`ticket-${ticket.id}`}>
    <TicketContent ticket={ticket} onOpen={onOpen} />
    {draggable && <button {...attributes} {...listeners} className="drag-handle" aria-label={`Move ${ticket.title}`} title="Drag between Backlog and Ready, or reorder"><GripVertical size={15} /></button>}
  </article>;
}

function BoardColumn({ status, color, hint, tickets, onOpen, onCreate }: { status: Status; color: string; hint: string; tickets: Ticket[]; onOpen(ticket: Ticket): void; onCreate(status: Status): void }) {
  const { setNodeRef, isOver } = useDroppable({ id: status, data: { type: 'Column', status }, disabled: !humanState(status) });
  return <section ref={setNodeRef} className={`board-column ${isOver ? 'column-over' : ''}`} aria-label={`${status} column`}>
    <div className="column-heading"><span className={`status-dot ${color}`} /><h2>{status}</h2><span className="column-count">{tickets.length}</span>{humanState(status) && <button className="icon-button column-add" aria-label={`Add ticket to ${status}`} onClick={() => onCreate(status)}><Plus size={16} /></button>}</div>
    <SortableContext items={tickets.map(ticket => ticket.id)} strategy={verticalListSortingStrategy}>
      <div className="column-cards">{tickets.map(ticket => <TicketCard key={ticket.id} ticket={ticket} onOpen={() => onOpen(ticket)} />)}</div>
    </SortableContext>
    {!tickets.length && <div className="column-empty"><div className="empty-line" /><p>{hint}</p></div>}
  </section>;
}

export function Board({ tickets, onOpen, onCreate, onMove }: { tickets: Ticket[]; onOpen(ticket: Ticket): void; onCreate(status: Status): void; onMove(ticket: Ticket, patch: TicketPatch): Promise<void> }) {
  const [active, setActive] = useState<Ticket | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }));
  async function drop({ active: dragged, over }: DragEndEvent) {
    setActive(null);
    if (!over || dragged.id === over.id) return;
    const ticket = tickets.find(item => item.id === dragged.id);
    const targetTicket = tickets.find(item => item.id === over.id);
    const status = targetTicket?.status ?? over.data.current?.status as Status | undefined;
    if (!ticket || !status || !humanState(ticket.status ?? 'Backlog') || !humanState(status)) return;
    const destination = sortTickets(tickets.filter(item => item.status === status && item.id !== ticket.id));
    const index = targetTicket ? destination.findIndex(item => item.id === targetTicket.id) : destination.length;
    const before = destination[index - 1]?.position ?? (destination[index]?.position ?? 0) - 2;
    const after = destination[index]?.position ?? before + 2;
    await onMove(ticket, { status, position: (before + after) / 2 });
  }
  return <DndContext sensors={sensors} onDragStart={({ active }) => setActive(active.data.current?.ticket ?? null)} onDragCancel={() => setActive(null)} onDragEnd={drop}
    accessibility={{ announcements: { onDragStart: ({ active }) => `Picked up ticket ${active.data.current?.ticket?.title ?? ''}. Move between Backlog and Ready.`, onDragOver: ({ over }) => over ? `Moving over ${over.data.current?.status ?? over.data.current?.ticket?.status ?? 'column'}.` : undefined, onDragEnd: ({ over }) => over ? `Dropped ticket in ${over.data.current?.status ?? over.data.current?.ticket?.status ?? 'column'}.` : 'Move cancelled.', onDragCancel: () => 'Move cancelled.' } }}>
    <div className="board-scroll"><div className="board">{columns.map(column => <BoardColumn key={column.status} {...column} tickets={sortTickets(tickets.filter(ticket => ticket.status === column.status))} onOpen={onOpen} onCreate={onCreate} />)}</div></div>
    <DragOverlay>{active && <article className="ticket-card drag-overlay"><TicketContent ticket={active} /></article>}</DragOverlay>
  </DndContext>;
}
