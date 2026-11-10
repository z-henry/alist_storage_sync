const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

// A small DOM fixture exercises the production rendering functions without
// opening a browser or connecting to any running service.
class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.ownText = '';
    this.value = '';
    this.dataset = {};
    this.style = {};
    this.events = {};
  }
  set textContent(value) { this.ownText = String(value); this.children = []; }
  get textContent() { return this.ownText + this.children.map(c => c.textContent).join(' '); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.ownText = ''; this.children = children; }
  addEventListener(event, callback) { this.events[event] = callback; }
  setAttribute() {}
  matches(selector) {
    if (selector.startsWith('.')) return this.className?.split(' ').includes(selector.slice(1));
    const field = selector.match(/^\[data-config-field="([^"]+)"\]$/);
    if (field) return this.dataset.configField === field[1];
    return this.tag === selector;
  }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [
      ...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector),
    ]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function fixture() {
  const source = fs.readFileSync(path.join(__dirname, '../static/ui.js'), 'utf8');
  const elements = new Map();
  const elementById = id => {
    if (!elements.has(id)) elements.set(id, new Element('div'));
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {
      querySelector: () => null,
      getElementById: elementById,
      querySelectorAll: selector => {
        const match = selector.match(/^#([\w-]+) (.+)$/);
        return match ? elementById(match[1]).querySelectorAll(match[2]) : [];
      },
      createElement: (tag) => new Element(tag),
    },
    Node: Element, console, URL, queueMicrotask: () => {},
  });
  // Keep startup listeners/network polling out of this isolated fixture.
  vm.runInContext(source.slice(0, source.lastIndexOf('\ndocument.querySelectorAll(".nav-item")')), context);
  return context;
}

test('deletion-only run shows individual success and failure instead of an empty task', () => {
  const context = fixture();
  const panel = new Element('div');
  context.renderRunChildren(panel, {
    task_type: 'sync', task_uuid: '3', run_id: 'run-a',
    result: { scanned_entries: 2, copy_requests_submitted: 0, source_files_deleted: 1,
      source_files_delete_failed: 1, failures: 1 },
    source_deletion_summary: { total: 2, succeeded: 1, failed: 1 },
    source_deletions: [
      { entry_name: '<video>.mp4', source_dir: '/local', destination_dir: '/115', status: 'succeeded', total_bytes: 1024 },
      { entry_name: 'failed.mp4', source_dir: '/local', destination_dir: '/115', status: 'failed', error: 'request failed' },
    ],
  });
  assert.match(panel.textContent, /源文件删除：成功 1 · 失败 1/);
  assert.match(panel.textContent, /<video>.mp4/);
  assert.match(panel.textContent, /已删除/);
  assert.match(panel.textContent, /failed.mp4/);
  assert.match(panel.textContent, /删除失败/);
  assert.match(panel.textContent, /request failed/);
  assert.doesNotMatch(panel.textContent, /暂无.*记录|历史记录/);
});

test('older deletion-only run displays recorded totals without inventing filenames', () => {
  const context = fixture();
  const panel = new Element('div');
  context.renderRunChildren(panel, {
    task_type: 'sync', task_uuid: '3',
    result: { scanned_entries: 89, copy_requests_submitted: 0, source_files_deleted: 12, failures: 0 },
    source_deletions: [], source_deletion_summary: { total: 0, succeeded: 0, failed: 0 },
  });
  assert.match(panel.textContent, /扫描 89/);
  assert.match(panel.textContent, /源文件删除：成功 12/);
  assert.match(panel.textContent, /历史记录中有 12 项仅保存了统计/);
  assert.doesNotMatch(panel.textContent, /暂无文件子任务记录/);
});

test('mixed busy group displays the actual trigger label', () => {
  const context = fixture();
  const table = new Element('table');
  context.renderRunsTable(table, [{
    overview_entry_type: 'group', task_type: 'sync', task_uuid: '3',
    trigger_type: 'mixed', run_count: 3, overview_kind: 'skipped_busy', status: 'skipped_busy',
  }]);
  assert.match(table.textContent, /多种触发/);
  assert.match(table.textContent, /忙碌跳过 × 3/);
  assert.doesNotMatch(table.textContent, /定时触发/);
});

test('mixed copy/deletion pagination and periodic refresh retain every loaded record', async () => {
  const context = fixture();
  const requests = [];
  const copies = Array.from({ length: 3 }, (_, i) => ({ alist_task_id: `c${i}` }));
  const deletions = Array.from({ length: 650 }, (_, i) => ({ deletion_id: i }));
  context.getJson = async (url) => {
    const query = new URL(url, 'http://test').searchParams;
    const offset = Number(query.get('offset'));
    const limit = Math.min(Number(query.get('limit')), 500);
    requests.push({ offset, limit });
    const run = { run_id: 'r' };
    for (const [key, entries] of [['alist_tasks', copies], ['source_deletions', deletions]]) {
      run[key] = entries.slice(offset, offset + limit);
      run[`${key}_page`] = { offset, limit, total: entries.length, returned: run[key].length,
        truncated: offset + run[key].length < entries.length };
    }
    return { run };
  };
  let detail = await context.loadRunDetail('r');
  for (let i = 0; i < 5; i++) detail = await context.loadRunDetail('r', true);
  assert.equal(detail.source_deletions.length, 600);
  detail = await context.loadRunDetail('r');
  assert.equal(requests.at(-1).limit, 500);
  assert.equal(detail.source_deletions.length, 600);
  detail = await context.loadRunDetail('r', true);
  assert.equal(detail.source_deletions.length, 650);
  assert.equal(detail.alist_tasks.length, 3);
  assert.equal(new Set(detail.source_deletions.map(d => d.deletion_id)).size, 650);
  assert.equal(detail.source_deletions_page.truncated, false);
});

test('copy and deletion outcomes both appear in the collapsed row', () => {
  const context = fixture();
  const text = context.alistTaskCell({
    alist_task_summary: { succeeded: 2, failed: 0, total: 2, progress: 100 },
    result: { source_files_deleted: 43, source_files_delete_failed: 5 },
  });
  assert.match(text, /上传 2\/2/);
  assert.match(text, /源文件删除 43 成功 \/ 5 失败/);
});


test('custom task name appears in the homepage heading and run row', () => {
  const context = fixture();
  const run = { task_type: 'sync', task_uuid: '3', task_name: 'MP 整理目录 → 115',
    run_id: 'r', status: 'succeeded', created_at: '2026-11-10T07:10:00Z' };
  const cell = context.runNameCell(run);
  assert.equal(cell.children[0].textContent, 'MP 整理目录 → 115');
  assert.match(cell.textContent, /ID 3/);
  context.renderOverviewRunGroups([run]);
  const groups = context.document.getElementById('recent-runs-groups');
  assert.equal(groups.children[0].children[0].children[0].textContent, 'MP 整理目录 → 115');
});

test('all task forms edit names independently of the UUID and allow clearing', () => {
  const context = fixture();
  for (const [append, id] of [
    ['appendSyncTask', 'config-sync-tasks'], ['appendStrmTask', 'config-strm-tasks'],
    ['appendTreeTask', 'config-tree-tasks'],
  ]) {
    context[append]({ uuid: '3', name: '旧名称' });
    const card = context.document.getElementById(id).children[0];
    const input = card.querySelector('[data-config-field="name"]');
    assert.equal(input.required, false);
    assert.equal(input.value, '旧名称');
    input.value = '  新名称  ';
    card.events.input({ target: input });
    assert.equal(card.querySelector('.config-task-number').textContent, '新名称');
    assert.equal(context.taskValue(card, 'uuid'), '3');
  }
  const form = context.collectConfigForm();
  for (const key of ['tasks', 'alist2strm_tasks', 'dir_tree_build_tasks']) {
    assert.equal(form[key][0].name, '新名称');
    assert.equal(form[key][0].uuid, '3');
  }
  const card = context.document.getElementById('config-sync-tasks').children[0];
  const input = card.querySelector('[data-config-field="name"]');
  input.value = '';
  card.events.input({ target: input });
  assert.equal(card.querySelector('.config-task-number').textContent, '同步任务 1');
  assert.equal(context.collectConfigForm().tasks[0].name, '');
});
