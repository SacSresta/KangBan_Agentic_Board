import { test, expect, _electron as electron, type ElectronApplication } from '@playwright/test';
import { spawn, execFileSync, type ChildProcess } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const root = path.resolve(__dirname, '../..');
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const baseURL = 'http://127.0.0.1:8765';
const cleanEnvironment = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')));
let service: ChildProcess | undefined;
let temporary: string;
let repository: string;
let data: string;
let projectId: string;
let ticketId: string;

function git(...args: string[]) { return execFileSync('git', ['-C', repository, ...args], { env: cleanEnvironment, encoding: 'utf8' }).trim(); }
async function available() {
  try { return (await fetch(`${baseURL}/api/health`, { signal: AbortSignal.timeout(1000) })).ok; } catch { return false; }
}
async function stop() {
  const child = service; service = undefined;
  if (!child || child.exitCode !== null) return;
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('Backend shutdown exceeded 10 seconds')); }, 10000);
    child.once('exit', () => { clearTimeout(timer); resolve(); });
    child.kill('SIGTERM');
  });
}
async function launchDesktop(): Promise<ElectronApplication> {
  return electron.launch({ args: [path.join(root, 'electron/main.cjs')], env: { ...cleanEnvironment, KANBAN_DATA_DIR: data, KANBAN_PYTHON: python } as Record<string, string>, timeout: 30000 });
}

test.describe.serial('Real worktree and persisted browser/desktop integration', () => {
  test.beforeAll(async () => {
    if (await available()) throw new Error('Port 8765 must be free; these tests use their own temporary databases.');
    temporary = mkdtempSync(path.join(tmpdir(), 'kanban-e2e-'));
    repository = path.join(temporary, 'repo'); data = path.join(temporary, 'data'); mkdirSync(repository);
    git('init', '-b', 'main'); git('config', 'user.name', 'E2E Fixture'); git('config', 'user.email', 'fixture@example.invalid');
    writeFileSync(path.join(repository, 'value.txt'), 'old');
    writeFileSync(path.join(repository, 'check.py'), "from pathlib import Path\nassert Path('value.txt').read_text() == 'new'\n");
    git('add', '.'); git('commit', '-m', 'Initial fixture');
    service = spawn(python, ['-m', 'kanban', '--data-dir', data], { cwd: root, env: cleanEnvironment, stdio: 'ignore' });
    await expect.poll(available, { timeout: 20000 }).toBe(true);
  });
  test.afterAll(async () => { await stop(); });

  test('browser creates, edits, reviews, requests changes, approves and confirms a manual merge', async ({ page, request }) => {
    const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    const initial = git('rev-parse', 'main');
    await page.goto('/');
    await expect(page.getByText('Deterministic test mode', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Connect repository', exact: true }).click();
    await page.getByLabel('Project name', { exact: true }).fill('Integration workspace');
    await page.getByLabel(/^Repository path/).fill(repository);
    await page.getByLabel(/^Approved test commands/).fill(JSON.stringify([[python, 'check.py']]));
    await page.getByRole('button', { name: 'Add repository', exact: true }).last().click();
    await expect(page.getByText('Repository settings saved.', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Close dialog' }).click();
    projectId = (await (await request.get('/api/projects')).json())[0].id;
    await page.getByRole('button', { name: 'New ticket', exact: true }).click();
    await page.getByLabel('Title', { exact: true }).fill('Update the fixture');
    const script = '```test-script\n' + JSON.stringify({ edits: [{ path: 'value.txt', content: 'new' }], review_verdicts: ['approve'] }) + '\n```';
    await page.getByLabel('Description', { exact: true }).fill(script);
    await page.getByLabel(/^Acceptance criteria/).fill('Value becomes new and real tests pass');
    await page.getByLabel(/^Priority/).selectOption('1');
    await page.getByRole('button', { name: 'Create ticket', exact: true }).click();
    await page.getByRole('button', { name: 'Update the fixture', exact: true }).click();
    await page.getByRole('button', { name: 'Edit ticket', exact: true }).click();
    await page.getByLabel('Title', { exact: true }).fill('Reviewed fixture change');
    await page.getByRole('button', { name: 'Save ticket', exact: true }).click();
    const tickets = await (await request.get('/api/tickets')).json(); ticketId = tickets[0].id;
    await request.post('/api/tickets', { data: { project_id: projectId, title: 'Depends on fixture', dependencies: [ticketId] } });
    await expect(page.getByRole('button', { name: 'Depends on fixture', exact: true })).toBeVisible();
    for (const destination of ['Ready', 'Backlog']) {
      const handle = page.getByRole('button', { name: 'Move Depends on fixture', exact: true });
      const source = await handle.boundingBox();
      const target = await page.getByRole('region', { name: `${destination} column`, exact: true }).boundingBox();
      if (!source || !target) throw new Error('Drag targets are not visible');
      await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
      await page.mouse.down();
      await page.mouse.move(source.x + source.width / 2 + 9, source.y + source.height / 2, { steps: 3 });
      await page.mouse.move(target.x + target.width / 2, target.y + 100, { steps: 12 });
      await page.mouse.up();
      await expect(page.getByRole('region', { name: `${destination} column`, exact: true }).getByRole('button', { name: 'Depends on fixture', exact: true })).toBeVisible();
    }
    await page.getByRole('button', { name: 'Reviewed fixture change', exact: true }).click();
    await page.getByRole('button', { name: 'Move to Ready', exact: true }).click();
    await page.getByRole('button', { name: 'Close dialog' }).click();
    await page.getByRole('button', { name: 'Start queue', exact: true }).click();
    await expect(page.getByRole('region', { name: 'Awaiting Human column', exact: true }).getByRole('button', { name: 'Reviewed fixture change', exact: true })).toBeVisible({ timeout: 15000 });
    await page.getByRole('button', { name: 'Pause queue', exact: true }).click();
    await page.getByRole('button', { name: 'Reviewed fixture change', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Approve artifact', exact: true })).toBeVisible();
    const oldRun = (await (await request.get(`/api/runs?ticket_id=${ticketId}`)).json())[0];
    await page.getByRole('tab', { name: 'Patch', exact: true }).click();
    await expect(page.locator('.patch-output')).toContainText('+new');
    await page.getByRole('tab', { name: 'Test output', exact: true }).click();
    await expect(page.getByRole('tabpanel')).toContainText('returncode');
    await page.getByLabel('Review feedback', { exact: true }).fill('Repeat test and review evidence.');
    await page.getByRole('button', { name: 'Request changes', exact: true }).click();
    await expect.poll(async () => (await (await request.get(`/api/runs/${oldRun.id}`)).json()).artifact_commit, { timeout: 15000 }).not.toBe(oldRun.artifact_commit);
    await expect(page.getByRole('button', { name: 'Approve artifact', exact: true })).toBeVisible({ timeout: 15000 });
    expect((await request.post(`/api/runs/${oldRun.id}/approve`, { data: { artifact_commit: oldRun.artifact_commit } })).status()).toBe(409);
    await page.getByRole('button', { name: 'Approve artifact', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Confirm manual merge', exact: true })).toBeVisible();
    expect(git('rev-parse', 'main')).toBe(initial);
    const run = await (await request.get(`/api/runs/${oldRun.id}`)).json();
    expect(run.approved_commit).toBe(run.artifact_commit);
    expect((await request.post(`/api/runs/${run.id}/confirm-merge`, { data: { artifact_commit: run.approved_commit } })).status()).toBe(409);
    git('merge', '--ff-only', run.branch);
    await page.getByRole('button', { name: 'Confirm manual merge', exact: true }).click();
    await page.getByRole('button', { name: 'Close dialog' }).click();
    await expect(page.getByRole('region', { name: 'Done column', exact: true }).getByRole('button', { name: 'Reviewed fixture change', exact: true })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole('button', { name: 'New ticket', exact: true })).toBeVisible();
    await expect(page.getByLabel('Current project on mobile', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Add repository on mobile', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.setViewportSize({ width: 1500, height: 960 });
    await page.screenshot({ path: 'test-results/board.png', fullPage: true });
    expect(errors).toEqual([]);
  });

  test('Electron reuses a matching service and leaves it running on exit', async () => {
    const desktop = await launchDesktop();
    try {
      const window = await desktop.firstWindow();
      await expect(window.getByRole('button', { name: 'Reviewed fixture change', exact: true })).toBeVisible();
      const browserWindow = await desktop.browserWindow(window);
      const preferences = await browserWindow.evaluate(win => win.webContents.getLastWebPreferences());
      expect(preferences.nodeIntegration).toBe(false); expect(preferences.contextIsolation).toBe(true); expect(preferences.sandbox).toBe(true);
      expect(await window.evaluate(() => typeof (window as unknown as { require?: unknown }).require)).toBe('undefined');
      expect(await window.evaluate(() => Object.keys((window as unknown as { kanbanDesktop: object }).kanbanDesktop))).toEqual(['selectRepository']);
    } finally { await desktop.close(); }
    expect(await available()).toBe(true);
  });

  test('owned desktop shutdown/reopen recovers an interrupted real test without replay', async ({ request }) => {
    await request.put(`/api/projects/${projectId}`, { data: { name: 'Integration workspace', repository_path: repository, base_branch: 'main', test_commands: [[python, '-c', 'import time; time.sleep(30)']] } });
    const ticket = await (await request.post('/api/tickets', { data: { project_id: projectId, title: 'Interrupt during test', status: 'Ready' } })).json();
    await stop();
    let desktop = await launchDesktop();
    try {
      const window = await desktop.firstWindow();
      await expect(window.getByRole('button', { name: 'Reviewed fixture change', exact: true })).toBeVisible();
      await window.getByRole('button', { name: 'Start queue', exact: true }).click();
      await expect.poll(async () => {
        const runs = await (await request.get(`/api/runs?ticket_id=${ticket.id}`)).json();
        const events = runs[0] ? await (await request.get(`/api/runs/${runs[0].id}/activity`)).json() : [];
        return events.some((event: { event_type: string; message: string }) => event.event_type === 'tool_start' && event.message.startsWith('run_test:'));
      }, { timeout: 10000 }).toBe(true);
    } finally { await desktop.close(); }
    await expect.poll(available, { timeout: 12000 }).toBe(false);
    desktop = await launchDesktop();
    try {
      const window = await desktop.firstWindow();
      await expect(window.getByRole('region', { name: 'Blocked column', exact: true }).getByRole('button', { name: 'Interrupt during test', exact: true })).toBeVisible();
      const run = (await (await request.get(`/api/runs?ticket_id=${ticket.id}`)).json())[0];
      const before = await (await request.get(`/api/runs/${run.id}/activity`)).json();
      await window.getByRole('button', { name: 'Interrupt during test', exact: true }).click();
      await expect(window.getByRole('button', { name: 'Resume blocked work', exact: true })).toBeDisabled();
      expect((await (await request.get('/api/queue')).json()).enabled).toBe(false);
      expect((await (await request.get(`/api/runs/${run.id}`)).json()).tool_calls).toBe(run.tool_calls);
      expect((await (await request.get(`/api/runs/${run.id}/activity`)).json()).length).toBeGreaterThanOrEqual(before.length);
    } finally { await desktop.close(); }
    await expect.poll(available, { timeout: 12000 }).toBe(false);
  });
});
