'use client';

import { useEffect, useState, type FormEvent } from 'react';
import { CheckCircle2, FolderOpen, LoaderCircle, PlugZap, Save } from 'lucide-react';
import { api } from '../lib/api';
import type { Project, ProviderCheck, Settings } from '../lib/contracts';
import { Modal } from './modal';

type Provider = NonNullable<Settings['engineer']>;
const defaultProvider: Provider = { kind: 'deterministic', model: 'scripted-test-provider', base_url: null, credential_env: null };

export function SettingsPanel({ project, onClose, onSaved }: { project?: Project; onClose(): void; onSaved(project?: Project): Promise<void> }) {
  const [name, setName] = useState(project?.name ?? 'My project');
  const [repository, setRepository] = useState(project?.repository_path ?? '');
  const [branch, setBranch] = useState(project?.base_branch ?? 'main');
  const [commands, setCommands] = useState(JSON.stringify(project?.test_commands ?? [], null, 2));
  const [settings, setSettings] = useState<Settings>({ engineer: defaultProvider, frontier: defaultProvider });
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [desktop, setDesktop] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [checks, setChecks] = useState<Partial<Record<'engineer' | 'frontier', ProviderCheck>>>({});
  useEffect(() => { setDesktop(!!window.kanbanDesktop); api.settings().then(value => { setSettings(value); setSettingsLoaded(true); }).catch(error => setError(error.message)); }, []);

  async function saveProject(event: FormEvent) {
    event.preventDefault(); setBusy('project'); setError(''); setNotice('');
    try {
      const parsed: unknown = JSON.parse(commands);
      if (!Array.isArray(parsed) || parsed.some(command => !Array.isArray(command) || !command.length || command.some(argument => typeof argument !== 'string' || !argument))) throw new Error('Test commands must be a JSON array of nonempty argument arrays. Example: [["python", "-m", "pytest"]]');
      const input = { name, repository_path: repository, base_branch: branch, test_commands: parsed as string[][] };
      const saved = project ? await api.updateProject(project.id, input) : await api.createProject(input);
      await onSaved(saved); setNotice('Repository settings saved.');
    } catch (error) { setError(error instanceof Error ? error.message : 'Could not save repository'); }
    finally { setBusy(null); }
  }
  async function saveModels() {
    setBusy('models'); setError(''); setNotice('');
    try { setSettings(await api.saveSettings(settings)); setChecks({}); setNotice('Model connections saved. Check each connection to verify capabilities.'); await onSaved(); }
    catch (error) { setError(error instanceof Error ? error.message : 'Could not save connections'); }
    finally { setBusy(null); }
  }
  async function check(role: 'engineer' | 'frontier') {
    setBusy(role); setError('');
    try { await api.saveSettings(settings); const result = await api.checkProvider(role); setChecks(previous => ({ ...previous, [role]: result })); await onSaved(); }
    catch (error) { setError(error instanceof Error ? error.message : 'Connection check failed'); }
    finally { setBusy(null); }
  }
  function updateProvider(role: 'engineer' | 'frontier', patch: Partial<Provider>) {
    setSettings(previous => ({ ...previous, [role]: { ...previous[role], ...patch } }));
    setChecks(previous => ({ ...previous, [role]: undefined }));
  }
  return <Modal title="Workspace settings" eyebrow="Connections & execution" onClose={onClose} wide><div className="modal-body settings-body">
    <form onSubmit={saveProject} className="form-stack settings-section"><div className="section-heading"><FolderOpen size={19} /><div><h3>Repository</h3><p>Agents use a separate branch and worktree for each ticket.</p></div></div>
      <div className="form-grid"><label>Project name<input required value={name} onChange={event => setName(event.target.value)} /></label><label>Base branch<input required value={branch} onChange={event => setBranch(event.target.value)} placeholder="main" /></label></div>
      <label>Repository path<div className="input-with-button"><input required value={repository} onChange={event => setRepository(event.target.value)} placeholder="/absolute/path/to/your/repository" />{desktop && <button type="button" className="button secondary" onClick={async () => { const selected = await window.kanbanDesktop?.selectRepository(); if (selected) setRepository(selected); }}><FolderOpen size={15} />Browse</button>}</div><span className="field-hint">Select a Git repository with an initial commit.</span></label>
      <label>Approved test commands (JSON)<textarea className="code-input" rows={5} value={commands} onChange={event => setCommands(event.target.value)} placeholder={'[["python", "-m", "pytest"]]'} /><span className="field-hint">Each command is an argument array, for example [["python", "-m", "pytest"]]. Commands run in the worktree with a 120 second limit.</span></label>
      <div><button className="button secondary" disabled={!!busy}><Save size={15} />{busy === 'project' ? 'Saving…' : project ? 'Save repository' : 'Add repository'}</button></div>
    </form>
    <section className="settings-section form-stack"><div className="section-heading"><PlugZap size={19} /><div><h3>Model connections</h3><p>Configure the local engineer and mandatory frontier reviewer independently.</p></div></div>
      {(['engineer', 'frontier'] as const).map(role => {
        const config = settings[role] ?? defaultProvider;
        const result = checks[role];
        return <fieldset className="provider-field" key={role}><legend>{role === 'engineer' ? 'Engineer' : 'Frontier reviewer'}</legend>
          <div className="form-grid"><label>{role === 'engineer' ? 'Engineer provider' : 'Frontier provider'}<select value={config.kind} onChange={event => updateProvider(role, { kind: event.target.value as Provider['kind'], model: event.target.value === 'deterministic' ? 'scripted-test-provider' : '', base_url: null })}><option value="deterministic">Deterministic test provider</option><option value="openai">OpenAI compatible / vLLM</option><option value="anthropic">Anthropic</option></select></label><label>{role === 'engineer' ? 'Engineer model' : 'Frontier model'}<input value={config.model} onChange={event => updateProvider(role, { model: event.target.value })} disabled={config.kind === 'deterministic'} placeholder="Served model name" /></label></div>
          {config.kind === 'deterministic' ? <p className="test-note">Scripted test mode exercises real repository tools. Results do not represent live model verification.</p> : <div className="form-grid"><label>{role === 'engineer' ? 'Engineer endpoint' : 'Frontier endpoint'}<input type="url" value={config.base_url ?? ''} onChange={event => updateProvider(role, { base_url: event.target.value || null })} placeholder={config.kind === 'openai' ? 'http://127.0.0.1:8000/v1' : 'https://api.anthropic.com'} /></label><label>{role === 'engineer' ? 'Engineer credential environment variable' : 'Frontier credential environment variable'}<input value={config.credential_env ?? ''} onChange={event => updateProvider(role, { credential_env: event.target.value || null })} placeholder={config.kind === 'openai' ? 'OPENAI_API_KEY' : 'ANTHROPIC_API_KEY'} /><span className="field-hint">Variable name only. Set its value in kanban_agent_board/.env, then restart the backend.</span></label></div>}
          <button type="button" className="button secondary small" disabled={!!busy || !settingsLoaded} onClick={() => check(role)}>{busy === role ? <LoaderCircle className="spin" size={14} /> : <PlugZap size={14} />}{busy === role ? 'Checking capabilities…' : `Check ${role === 'engineer' ? 'engineer' : 'frontier'} connection`}</button>
          {result && <div className={`provider-result ${result.ok ? 'result-ok' : 'result-error'}`}><p><CheckCircle2 size={14} />{result.message}</p><div className="capabilities"><span>Chat: {result.chat ? 'passed' : 'failed'}</span><span>Tools: {result.tool_calling ? 'passed' : 'failed'}</span><span>Structured response: {result.structured_response ? 'passed' : 'failed'}</span></div></div>}
        </fieldset>;
      })}
      <div><button type="button" className="button primary" disabled={!!busy || !settingsLoaded} onClick={saveModels}><Save size={15} />{busy === 'models' ? 'Saving…' : 'Save model connections'}</button></div>
    </section>
    {error && <p className="form-error" role="alert">{error}</p>}{notice && <p className="form-success" role="status">{notice}</p>}
  </div></Modal>;
}
