const labels = {
  queued: "排队中",
  running: "运行中",
  waiting_alist: "等待 AList",
  submitted: "已提交 AList",
  succeeded: "成功",
  failed: "失败",
  skipped_busy: "忙碌跳过",
  skipped_unavailable: "AList 不可用",
  interrupted: "意外中断",
  scheduled: "定时触发",
  postprocess: "同步后触发",
  api: "API 触发",
  manual: "手动触发",
  sync: "存储同步",
  cache_refresh: "子任务巡检与后处理",
  dir_tree_build: "目录树刷新",
  alist2strm: "STRM 生成",
};

const viewMeta = {
  overview: ["OPERATIONS / OVERVIEW", "运行概览"],
  tasks: ["OPERATIONS / SCHEDULES", "定时任务"],
  runs: ["OPERATIONS / RUNS", "运行记录"],
  requests: ["OPERATIONS / INBOUND", "API 请求"],
  callbacks: ["OPERATIONS / OUTBOUND", "回调记录"],
  config: ["OPERATIONS / CONFIGURATION", "配置管理"],
};

let currentView = "overview";
let refreshing = false;
let configLoaded = false;
let configDirty = false;
let configWritable = false;
let loadedConfig = null;
const expandedRunIds = new Set();
const expandedOverviewGroups = new Set();
const runDetailCache = new Map();
const runDetailRequests = new Map();
const childPageSize = 100;
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

function handleAuthentication(response) {
  if (response.status === 401) {
    const next = `${window.location.pathname}${window.location.hash}`;
    window.location.assign(`/ui/login?next=${encodeURIComponent(next)}`);
    throw new Error("登录已失效，请重新登录");
  }
  return response;
}

function writeHeaders(extra = {}) {
  return { ...extra, "X-CSRF-Token": csrfToken };
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function label(value) {
  return labels[value] || value || "—";
}

function instanceKey(taskType, taskUuid) {
  return JSON.stringify([taskType, String(taskUuid)]);
}

function parseInstanceKey(value) {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) && parsed.length === 2 ? parsed : null;
  } catch (_error) {
    return null;
  }
}

function instanceLabel(taskType, taskUuid) {
  if (taskType === "sync") return `同步任务 · ${taskUuid}`;
  if (taskType === "alist2strm") return `STRM 生成 · ${taskUuid}`;
  if (taskType === "cache_refresh") return `子任务巡检与后处理 · ${taskUuid}`;
  if (taskType === "dir_tree_build") return `目录树刷新 · ${taskUuid}`;
  return `${label(taskType)} · ${taskUuid}`;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).format(date);
}

function duration(record) {
  if (!record.started_at) return "—";
  const end = record.finished_at ? new Date(record.finished_at) : new Date();
  const milliseconds = end - new Date(record.started_at);
  if (milliseconds < 1000) return `${Math.max(0, milliseconds)} ms`;
  if (milliseconds < 60000) return `${(milliseconds / 1000).toFixed(1)} s`;
  return `${(milliseconds / 60000).toFixed(1)} min`;
}

function formatBytes(value) {
  if (value == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = Number(value);
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function statusNode(status) {
  return el("span", `status ${status || ""}`, label(status));
}

function getStrmResult(data) {
  if (!data || data.task_type !== "alist2strm") return null;
  return data.result || {};
}

function legacyDuplicateErrors(result) {
  return (result.errors || []).filter((item) =>
    String(item?.error || "").includes("Multiple source files map to the same output"));
}

function duplicateGroupCount(result) {
  if (result.duplicate_groups !== undefined) return Number(result.duplicate_groups) || 0;
  return legacyDuplicateErrors(result).length;
}

function strmResultSummaryText(result) {
  const duplicateGroups = duplicateGroupCount(result);
  const duplicateCopy = duplicateGroups
    ? result.duplicate_groups === undefined
      ? ` · 旧版同名输出错误 ${duplicateGroups} 条`
      : ` · 同名输出 ${duplicateGroups} 组（已有跳过 ${result.duplicate_existing_groups || 0}，选择最大文件 ${result.duplicate_selected_groups || 0}）`
    : " · 无同名输出冲突";
  return `扫描 ${result.scanned || 0} · 创建 ${result.strm_created || 0} · 已有跳过 ${result.skipped_existing || 0}${duplicateCopy} · 失败 ${result.failed || 0}`;
}

function duplicateActionLabel(action) {
  if (action === "skipped_existing") return "目标已存在 · 全部跳过";
  if (action === "overwritten_with_largest") return "覆盖模式 · 已选择最大文件";
  return "目标不存在 · 已选择最大文件";
}

function createStrmResultSummary(result, detailLimit = 100) {
  const section = el("section", "strm-result-summary");
  const heading = el("div", "strm-result-heading");
  heading.append(
    el("strong", "", "STRM 处理摘要"),
    el("span", "", result.incremental ? "增量处理" : "完整扫描"),
  );
  section.append(heading);

  const metrics = el("div", "strm-result-metrics");
  [
    ["扫描文件", result.scanned || 0],
    ["创建 STRM", result.strm_created || 0],
    ["已有跳过", result.skipped_existing || 0],
    ["同名输出", duplicateGroupCount(result)],
    ["忽略源文件", result.duplicate_ignored_count || 0],
    ["处理失败", result.failed || 0],
  ].forEach(([title, value]) => {
    const metric = el("div", "strm-result-metric");
    metric.append(el("span", "", title), el("strong", "", value));
    metrics.append(metric);
  });
  section.append(metrics);

  const duplicateGroups = duplicateGroupCount(result);
  if (!duplicateGroups) {
    section.append(el("p", "strm-result-note", "本次运行没有发现多个源文件映射到同一个输出。"));
    return section;
  }

  if (result.duplicate_groups === undefined) {
    section.append(
      el(
        "p",
        "strm-result-note",
        `这是旧版运行记录，共有 ${duplicateGroups} 条同名输出错误；旧记录没有保存候选大小和自动选择结果。部署新版后的运行会按新规则处理并展示完整明细。`,
      ),
    );
    return section;
  }

  section.append(
    el(
      "p",
      "strm-result-note",
      `已有目标 ${result.duplicate_existing_groups || 0} 组全部跳过；${result.duplicate_selected_groups || 0} 组按文件体积选择最大源文件。`,
    ),
  );
  const list = el("div", "strm-duplicate-list");
  const details = result.duplicate_details || [];
  details.slice(0, detailLimit).forEach((detail) => {
    const card = el("article", "strm-duplicate-card");
    const cardHeading = el("div", "strm-duplicate-heading");
    cardHeading.append(
      el("code", "", detail.output_path),
      el("span", `strm-duplicate-action ${detail.action || ""}`, duplicateActionLabel(detail.action)),
    );
    card.append(cardHeading);
    const selectedPath = detail.selected?.path;
    const candidates = el("div", "strm-candidates");
    (detail.candidates || []).forEach((candidate) => {
      const selected = selectedPath === candidate.path;
      const row = el("div", `strm-candidate${selected ? " selected" : ""}`);
      const state = detail.action === "skipped_existing"
        ? "已跳过"
        : (selected ? "已选择" : "已忽略");
      row.append(
        el("span", "strm-candidate-state", state),
        el("code", "", candidate.path),
        el("span", "strm-candidate-size", formatBytes(candidate.size)),
      );
      candidates.append(row);
    });
    card.append(candidates);
    list.append(card);
  });
  section.append(list);

  const hidden = Math.max(0, details.length - detailLimit) + (result.duplicate_details_truncated || 0);
  if (hidden) {
    section.append(el("p", "strm-result-note", `还有 ${hidden} 组明细未在此处展开，请查看完整运行详情。`));
  }
  return section;
}

function showDetail(title, data) {
  document.getElementById("detail-title").textContent = title;
  const content = document.getElementById("detail-content");
  content.replaceChildren();
  const strmResult = getStrmResult(data);
  if (strmResult) content.append(createStrmResultSummary(strmResult));
  const raw = el(strmResult ? "details" : "div", strmResult ? "detail-json" : "");
  if (strmResult) raw.append(el("summary", "", "查看完整原始记录"));
  raw.append(el("pre", "", JSON.stringify(data, null, 2)));
  content.append(raw);
  document.getElementById("detail-dialog").showModal();
}

function showToast(message, kind = "error") {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.toggle("success", kind === "success");
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 3500);
}

async function getJson(url) {
  const response = handleAuthentication(await fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" }));
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function renderTable(targetId, columns, rows, detailTitle, detailLoader = null) {
  const table = document.getElementById(targetId);
  table.replaceChildren();
  const thead = el("thead");
  const headerRow = el("tr");
  columns.forEach((column) => headerRow.append(el("th", "", column.title)));
  thead.append(headerRow);
  table.append(thead);

  const tbody = el("tbody");
  if (!rows.length) {
    const row = el("tr");
    const cell = el("td", "empty", "暂无记录");
    cell.colSpan = columns.length;
    row.append(cell);
    tbody.append(row);
  } else {
    rows.forEach((item) => {
      const row = el("tr");
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      const openDetail = async () => {
        try {
          const detail = detailLoader ? await detailLoader(item) : item;
          showDetail(detailTitle(item), detail);
        } catch (error) {
          showToast(`读取详情失败：${error.message}`);
        }
      };
      row.addEventListener("click", openDetail);
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") openDetail();
      });
      columns.forEach((column) => {
        const cell = el("td");
        const value = column.render(item);
        if (value instanceof Node) cell.append(value);
        else cell.textContent = value ?? "—";
        row.append(cell);
      });
      tbody.append(row);
    });
  }
  table.append(tbody);
}

function runNameCell(run) {
  const wrap = el("div");
  wrap.append(el("span", "primary-cell", run.task_uuid));
  wrap.append(el("span", "secondary", `${label(run.task_type)} · ${label(run.trigger_type)}`));
  return wrap;
}

function alistTaskCell(run) {
  const summary = run.alist_task_summary;
  if (!summary) return "—";
  const completed = summary.succeeded + summary.failed;
  const progress = summary.progress == null ? "" : ` · ${summary.progress.toFixed(1)}%`;
  return `${completed}/${summary.total}${progress}`;
}

function childStatus(task) {
  if (task.status === "missing_timeout") return ["failed", "丢失超时"];
  if (task.state === 2) return ["succeeded", "成功"];
  if (task.state === 4) return ["failed", "已取消"];
  if (task.state === 7) return ["failed", "失败"];
  if (task.state === 0) return ["queued", "等待中"];
  return ["running", task.status || "运行中"];
}

function childProgress(task) {
  const value = typeof task.progress === "number" ? Math.max(0, Math.min(task.progress, 100)) : 0;
  const wrap = el("div", "child-progress");
  const track = el("span", "child-progress-track");
  const fill = el("span", "child-progress-fill");
  fill.style.width = `${value}%`;
  track.append(fill);
  wrap.append(track, el("small", "", `${value.toFixed(1)}%`));
  return wrap;
}

function childPanelTargets(runId) {
  return [...document.querySelectorAll("[data-run-children]")]
    .filter((node) => node.dataset.runChildren === runId);
}

function renderRunChildren(panel, detail) {
  panel.replaceChildren();
  const summary = detail.alist_task_summary;
  const strmResult = getStrmResult(detail);
  const toolbar = el("div", "child-toolbar");
  const copy = strmResult
    ? strmResultSummaryText(strmResult)
    : summary
    ? `成功 ${summary.succeeded} · 失败 ${summary.failed} · 等待 ${summary.pending} · 共 ${summary.total}`
    : "该父任务没有 AList 文件子任务";
  toolbar.append(el("span", "child-summary", copy));
  const detailButton = el(
    "button",
    "text-button",
    strmResult ? "查看完整运行详情" : "查看父任务参数与结果",
  );
  detailButton.type = "button";
  detailButton.addEventListener("click", () => {
    const { alist_tasks: _alistTasks, ...parentDetail } = detail;
    showDetail(`运行详情 · ${detail.task_uuid}`, parentDetail);
  });
  toolbar.append(detailButton);
  panel.append(toolbar);

  if (strmResult) {
    panel.append(createStrmResultSummary(strmResult, 5));
    return;
  }

  const tasks = detail.alist_tasks || [];
  if (!tasks.length) {
    panel.append(el("div", "child-empty", "暂无子任务记录"));
    return;
  }

  const wrap = el("div", "table-wrap child-table-wrap");
  const table = el("table", "child-table");
  const thead = el("thead");
  const header = el("tr");
  ["文件", "状态", "进度", "大小", "错误"].forEach((title) => header.append(el("th", "", title)));
  thead.append(header);
  table.append(thead);
  const tbody = el("tbody");
  tasks.forEach((task) => {
    const row = el("tr");
    const name = el("td");
    name.append(
      el("span", "primary-cell", task.entry_name || task.name || task.alist_task_id),
      el("span", "secondary", `${task.source_dir || "—"} → ${task.destination_dir || "—"}`),
    );
    const state = el("td");
    const [stateClass, stateLabel] = childStatus(task);
    state.append(statusNode(stateClass));
    state.querySelector(".status").textContent = stateLabel;
    const progress = el("td");
    progress.append(childProgress(task));
    row.append(
      name,
      state,
      progress,
      el("td", "", formatBytes(task.total_bytes)),
      el("td", task.error ? "child-error" : "", task.error || "—"),
    );
    tbody.append(row);
  });
  table.append(tbody);
  wrap.append(table);
  panel.append(wrap);

  if (detail.alist_tasks_page?.truncated) {
    const more = el("button", "load-more", `继续加载（已显示 ${tasks.length}/${detail.alist_tasks_page.total}）`);
    more.type = "button";
    more.addEventListener("click", () => loadRunDetail(detail.run_id, true));
    panel.append(more);
  }
}

function updateRunChildPanels(runId) {
  const detail = runDetailCache.get(runId);
  if (!detail) return;
  childPanelTargets(runId).forEach((panel) => renderRunChildren(panel, detail));
}

async function loadRunDetail(runId, loadMore = false) {
  if (!loadMore && runDetailRequests.has(runId)) return runDetailRequests.get(runId);
  const existing = runDetailCache.get(runId);
  const existingCount = existing?.alist_tasks?.length || 0;
  const offset = loadMore ? existingCount : 0;
  const limit = loadMore ? childPageSize : Math.max(childPageSize, Math.min(existingCount, 1000));
  if (existing && !loadMore) updateRunChildPanels(runId);
  childPanelTargets(runId).forEach((panel) => {
    if (!existing || loadMore) panel.textContent = loadMore ? "正在加载更多子任务…" : "正在加载子任务…";
  });

  const request = getJson(
    `/ui/api/runs/${encodeURIComponent(runId)}?limit=${limit}&offset=${offset}`,
  ).then(({ run }) => {
    if (loadMore && existing) {
      const mergedTasks = [...(existing.alist_tasks || []), ...(run.alist_tasks || [])];
      run.alist_tasks = mergedTasks;
      run.alist_tasks_page = {
        ...run.alist_tasks_page,
        offset: 0,
        returned: mergedTasks.length,
        truncated: mergedTasks.length < run.alist_tasks_page.total,
      };
    } else if (existing && existingCount > (run.alist_tasks || []).length) {
      const refreshedCount = (run.alist_tasks || []).length;
      const preservedTasks = (existing.alist_tasks || []).slice(
        refreshedCount,
        run.alist_tasks_page.total,
      );
      run.alist_tasks = [...(run.alist_tasks || []), ...preservedTasks];
      run.alist_tasks_page = {
        ...run.alist_tasks_page,
        offset: 0,
        returned: run.alist_tasks.length,
        truncated: run.alist_tasks.length < run.alist_tasks_page.total,
      };
    }
    runDetailCache.set(runId, run);
    updateRunChildPanels(runId);
    return run;
  }).catch((error) => {
    childPanelTargets(runId).forEach((panel) => {
      panel.textContent = `子任务加载失败：${error.message}`;
    });
    throw error;
  }).finally(() => runDetailRequests.delete(runId));

  if (!loadMore) runDetailRequests.set(runId, request);
  return request;
}

function renderRunsTable(target, runs) {
  const table = typeof target === "string" ? document.getElementById(target) : target;
  table.replaceChildren();
  const thead = el("thead");
  const header = el("tr");
  ["", "任务", "状态", "AList 子任务", "进入队列", "耗时", "运行 ID"]
    .forEach((title) => header.append(el("th", "", title)));
  thead.append(header);
  table.append(thead);
  const tbody = el("tbody");

  if (!runs.length) {
    const row = el("tr");
    const cell = el("td", "empty", "暂无运行记录");
    cell.colSpan = 7;
    row.append(cell);
    tbody.append(row);
  }

  runs.forEach((run) => {
    const expanded = expandedRunIds.has(run.run_id);
    const row = el("tr", "run-parent-row");
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-expanded", String(expanded));
    const toggleCell = el("td", "toggle-cell");
    const toggleButton = el("button", "fold-button", expanded ? "▼" : "▶");
    toggleButton.type = "button";
    toggleButton.setAttribute("aria-label", expanded ? "收起子任务" : "展开子任务");
    toggleCell.append(toggleButton);
    const name = el("td");
    name.append(runNameCell(run));
    const state = el("td");
    state.append(statusNode(run.status));
    row.append(
      toggleCell,
      name,
      state,
      el("td", "", alistTaskCell(run)),
      el("td", "", formatDate(run.created_at)),
      el("td", "", duration(run)),
      el("td", "run-id", run.run_id.slice(0, 8)),
    );

    const childRow = el("tr", "run-child-row");
    childRow.hidden = !expanded;
    const childCell = el("td", "run-child-cell");
    childCell.colSpan = 7;
    const panel = el("div", "run-children-panel");
    panel.dataset.runChildren = run.run_id;
    childCell.append(panel);
    childRow.append(childCell);

    const toggle = () => {
      const shouldExpand = !expandedRunIds.has(run.run_id);
      if (shouldExpand) expandedRunIds.add(run.run_id);
      else expandedRunIds.delete(run.run_id);
      row.setAttribute("aria-expanded", String(shouldExpand));
      toggleButton.textContent = shouldExpand ? "▼" : "▶";
      toggleButton.setAttribute("aria-label", shouldExpand ? "收起子任务" : "展开子任务");
      childRow.hidden = !shouldExpand;
      if (shouldExpand) loadRunDetail(run.run_id);
    };
    toggleButton.addEventListener("click", (event) => {
      event.stopPropagation();
      toggle();
    });
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle();
      }
    });
    tbody.append(row, childRow);
    if (expanded) queueMicrotask(() => loadRunDetail(run.run_id));
  });
  table.append(tbody);
}

function renderOverviewRunGroups(runs) {
  const container = document.getElementById("recent-runs-groups");
  container.replaceChildren();
  if (!runs.length) {
    container.append(el("div", "empty", "暂无运行记录"));
    return;
  }
  const groups = new Map();
  runs.forEach((run) => {
    const key = instanceKey(run.task_type, run.task_uuid);
    if (!groups.has(key)) {
      groups.set(key, {
        taskType: run.task_type,
        taskUuid: run.task_uuid,
        runs: [],
      });
    }
    groups.get(key).runs.push(run);
  });
  const order = ["sync", "alist2strm", "cache_refresh", "dir_tree_build"];
  [...groups.entries()].sort(([, a], [, b]) => {
    const ai = order.indexOf(a.taskType);
    const bi = order.indexOf(b.taskType);
    const typeOrder = (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
    return typeOrder || String(a.taskUuid).localeCompare(String(b.taskUuid), "zh-CN");
  }).forEach(([key, group]) => {
    const groupRuns = group.runs;
    const details = el("details", "run-group");
    details.open = expandedOverviewGroups.has(key);
    const summary = el("summary", "run-group-summary");
    const title = el(
      "span",
      "run-group-title",
      instanceLabel(group.taskType, group.taskUuid),
    );
    const latestCreatedAt = groupRuns.reduce(
      (latest, run) => (!latest || run.created_at > latest ? run.created_at : latest),
      "",
    );
    const failed = groupRuns.filter((run) => run.status === "failed").length;
  const waiting = groupRuns.filter((run) => ["queued", "running", "waiting_alist"].includes(run.status)).length;
    summary.append(
      title,
      el(
        "span",
        "run-group-meta",
        `${groupRuns.length} 个父任务 · 最近执行 ${formatDate(latestCreatedAt)} · 等待 ${waiting} · 失败 ${failed}`,
      ),
    );
    details.append(summary);
    const wrap = el("div", "table-wrap");
    const table = el("table");
    wrap.append(table);
    details.append(wrap);
    details.addEventListener("toggle", () => {
      if (details.open) expandedOverviewGroups.add(key);
      else expandedOverviewGroups.delete(key);
    });
    container.append(details);
    renderRunsTable(table, groupRuns);
  });
}

function populateRunInstanceFilter(configuredTasks, recentRuns) {
  const select = document.getElementById("run-instance-filter");
  const current = select.value;
  const instances = new Map();
  (configuredTasks || []).forEach((task) => {
    const key = instanceKey(task.task_type, task.task_uuid);
    instances.set(key, task.name || instanceLabel(task.task_type, task.task_uuid));
  });
  (recentRuns || []).forEach((run) => {
    const key = instanceKey(run.task_type, run.task_uuid);
    if (!instances.has(key)) {
      instances.set(key, instanceLabel(run.task_type, run.task_uuid));
    }
  });

  select.replaceChildren();
  const all = el("option", "", "全部实例任务");
  all.value = "";
  select.append(all);
  [...instances.entries()]
    .sort((a, b) => a[1].localeCompare(b[1], "zh-CN"))
    .forEach(([key, name]) => {
      const option = el("option", "", name);
      option.value = key;
      select.append(option);
    });
  if ([...select.options].some((option) => option.value === current)) {
    select.value = current;
  }
}

function renderOverview(data) {
  const runtime = data.runtime;
  const alist = runtime.alist || {};
  const serviceHealthy = runtime.scheduler_running && runtime.worker_alive;
  const healthy = serviceHealthy && alist.online;
  const liveDot = document.querySelector(".live-dot");
  document.querySelector(".hero-card").classList.toggle("offline", !alist.online);
  liveDot.className = `live-dot ${healthy ? "healthy" : "error"}`;
  document.getElementById("sidebar-service-state").textContent = alist.online
    ? "AList 在线 · 任务已启用"
    : "AList 不可用 · 任务已暂停";
  if (!serviceHealthy) {
    document.getElementById("hero-title").textContent = "服务组件需要检查";
    document.getElementById("hero-copy").textContent = "调度器或工作线程尚未启动，请检查应用启动日志。";
  } else if (!alist.online) {
    document.getElementById("hero-title").textContent = "AList 不可用，业务任务已暂停";
    document.getElementById("hero-copy").textContent = alist.error || "系统会持续探测，AList 恢复后自动继续调度。";
  } else {
    document.getElementById("hero-title").textContent = "AList 与任务服务运行正常";
    document.getElementById("hero-copy").textContent = `AList 响应 ${alist.latency_ms ?? "—"} ms，已载入 ${runtime.scheduler_jobs.length} 个调度计划。`;
  }
  document.getElementById("queue-number").textContent = runtime.queue_size;

  const counts = data.counts.runs_today || {};
  const metrics = [
    ["AList 状态", alist.online ? "在线" : "不可用", alist.checked_at ? `检测于 ${formatDate(alist.checked_at)}` : "等待首次检测"],
    ["等待 AList", (counts.waiting_alist || 0) + (counts.submitted || 0), "仍有复制子任务未结束"],
    ["今日成功", counts.succeeded || 0, "本地任务完整结束"],
    ["今日失败", counts.failed || 0, "需要查看错误详情"],
    ["回调失败", data.counts.callback_failures_today || 0, "Emby 与 Webhook"],
  ];
  const container = document.getElementById("metrics");
  container.replaceChildren();
  metrics.forEach(([title, value, hint]) => {
    const card = el("article", "metric-card");
    card.append(el("span", "", title), el("strong", "", value), el("small", "", hint));
    container.append(card);
  });
  renderOverviewRunGroups(data.recent_runs || []);
}

function cloneConfig(value) {
  return JSON.parse(JSON.stringify(value || {}));
}

function setConfigValue(id, value) {
  document.getElementById(id).value = value ?? "";
}

function createTaskField(labelText, field, value, options = {}) {
  const wrapper = el("label", `config-field${options.wide ? " config-field-wide" : ""}`);
  wrapper.append(el("span", "", labelText));
  const input = el("input");
  input.type = options.type || "text";
  input.dataset.configField = field;
  input.value = value ?? "";
  input.required = options.required !== false;
  if (options.placeholder) input.placeholder = options.placeholder;
  if (options.min !== undefined) input.min = options.min;
  if (options.max !== undefined) input.max = options.max;
  if (options.step !== undefined) input.step = options.step;
  wrapper.append(input);
  return wrapper;
}

function createTaskSelect(labelText, field, value, options) {
  const wrapper = el("label", "config-field");
  wrapper.append(el("span", "", labelText));
  const select = el("select");
  select.dataset.configField = field;
  Object.entries(options).forEach(([optionValue, optionLabel]) => {
    const option = el("option", "", optionLabel);
    option.value = optionValue;
    option.selected = optionValue === value;
    select.append(option);
  });
  wrapper.append(select);
  return wrapper;
}

function renumberConfigTasks(container, title) {
  [...container.querySelectorAll(".config-task-item")].forEach((card, index) => {
    card.querySelector(".config-task-number").textContent = `${title} ${index + 1}`;
  });
}

function appendSyncTask(task = {}) {
  const container = document.getElementById("config-sync-tasks");
  const card = el("article", "config-task-item");
  card.configOriginal = cloneConfig(task);
  const heading = el("div", "config-task-heading");
  heading.append(el("strong", "config-task-number", "同步任务"));
  const remove = el("button", "config-remove-button", "删除");
  remove.type = "button";
  remove.addEventListener("click", () => {
    card.remove();
    renumberConfigTasks(container, "同步任务");
    markConfigDirty();
  });
  heading.append(remove);
  const fields = el("div", "config-field-grid");
  fields.append(
    createTaskField("实例 UUID", "uuid", task.uuid, { placeholder: "例如 movie-pilot" }),
    createTaskField("Cron", "cron", task.cron ?? "0 * * * *", { placeholder: "0 * * * *" }),
    createTaskField("源路径", "src", task.src, { wide: true, placeholder: "/source/path" }),
    createTaskField("目标路径", "dst", task.dst, { wide: true, placeholder: "/target/path" }),
    createTaskField("本地挂载路径", "mounted_path", task.mounted_path ?? "", {
      wide: true,
      required: false,
      placeholder: "MoviePilot 回调需要时填写",
    }),
  );
  card.append(heading, fields);
  container.append(card);
  renumberConfigTasks(container, "同步任务");
}

function appendStrmTask(task = {}) {
  const container = document.getElementById("config-strm-tasks");
  const card = el("article", "config-task-item");
  card.configOriginal = cloneConfig(task);
  const heading = el("div", "config-task-heading");
  heading.append(el("strong", "config-task-number", "STRM 任务"));
  const remove = el("button", "config-remove-button", "删除");
  remove.type = "button";
  remove.addEventListener("click", () => {
    card.remove();
    renumberConfigTasks(container, "STRM 任务");
    markConfigDirty();
  });
  heading.append(remove);
  const fields = el("div", "config-field-grid");
  fields.append(
    createTaskField("实例 UUID", "uuid", task.uuid, { placeholder: "例如 movies-strm" }),
    createTaskField("Cron", "cron", task.cron ?? "0 */6 * * *", { placeholder: "0 */6 * * *" }),
    createTaskField("AList 源目录", "source_dir", task.source_dir, { wide: true, placeholder: "/媒体/电影" }),
    createTaskField("目标子目录", "target_dir", task.target_dir, { wide: true, placeholder: "movies" }),
    createTaskSelect("STRM 内容", "mode", task.mode ?? "alist_url", {
      alist_url: "AList 直链",
      raw_url: "存储原始链接",
      alist_path: "AList 路径",
    }),
    createTaskField("其他下载扩展名", "other_extensions", (task.other_extensions || []).join(","), {
      placeholder: ".xml,.txt",
      required: false,
    }),
    createTaskField("处理并发数", "max_workers", task.max_workers ?? 20, { type: "number", min: "1", max: "100", step: "1" }),
    createTaskField("下载并发数", "max_downloaders", task.max_downloaders ?? 3, { type: "number", min: "1", max: "20", step: "1" }),
  );
  const switches = el("div", "config-switches config-task-start");
  [
    ["flatten_mode", "平铺模式"],
    ["subtitle", "下载字幕"],
    ["image", "下载图片"],
    ["nfo", "下载 NFO"],
    ["overwrite", "覆盖已有文件"],
  ].forEach(([field, title]) => {
    const wrapper = el("label", "config-inline-switch");
    const input = el("input");
    input.type = "checkbox";
    input.dataset.configField = field;
    input.checked = Boolean(task[field]);
    wrapper.append(input, el("span", "", title));
    switches.append(wrapper);
  });
  card.append(heading, fields, switches);
  container.append(card);
  renumberConfigTasks(container, "STRM 任务");
}

function appendTreeTask(task = {}) {
  const container = document.getElementById("config-tree-tasks");
  const card = el("article", "config-task-item");
  card.configOriginal = cloneConfig(task);
  const heading = el("div", "config-task-heading");
  heading.append(el("strong", "config-task-number", "刷新任务"));
  const remove = el("button", "config-remove-button", "删除");
  remove.type = "button";
  remove.addEventListener("click", () => {
    card.remove();
    renumberConfigTasks(container, "刷新任务");
    markConfigDirty();
  });
  heading.append(remove);
  const fields = el("div", "config-field-grid");
  fields.append(
    createTaskField("实例 UUID", "uuid", task.uuid, { placeholder: "例如 refresh-115" }),
    createTaskField("Cron", "cron", task.cron ?? "0 12 * * *", { placeholder: "0 12 * * *" }),
    createTaskField("目录路径", "src", task.src, { wide: true, placeholder: "/115" }),
    createTaskField("QPS", "qps", task.qps ?? 0.1, { type: "number", min: "0.000001", step: "any" }),
  );
  const runAtStart = el("label", "config-inline-switch config-task-start");
  const checkbox = el("input");
  checkbox.type = "checkbox";
  checkbox.dataset.configField = "run_at_start";
  checkbox.checked = Boolean(task.run_at_start);
  runAtStart.append(checkbox, el("span", "", "应用启动时执行一次"));
  card.append(heading, fields, runAtStart);
  container.append(card);
  renumberConfigTasks(container, "刷新任务");
}

function taskValue(card, field) {
  const input = card.querySelector(`[data-config-field="${field}"]`);
  if (input.type === "checkbox") return input.checked;
  if (input.type === "number") return Number(input.value);
  return input.value.trim();
}

function collectConfigForm() {
  const result = cloneConfig(loadedConfig);
  result.alist = {
    ...(result.alist || {}),
    url: document.getElementById("config-alist-url").value.trim(),
    apikey: document.getElementById("config-alist-apikey").value,
    task_missing_timeout_seconds: Number(document.getElementById("config-task-timeout").value),
    request_timeout_seconds: Number(document.getElementById("config-request-timeout").value),
    healthcheck_interval_seconds: Number(document.getElementById("config-health-interval").value),
    healthcheck_timeout_seconds: Number(document.getElementById("config-health-timeout").value),
  };
  result.tasks = [...document.querySelectorAll("#config-sync-tasks .config-task-item")].map((card) => ({
    ...(card.configOriginal || {}),
    uuid: taskValue(card, "uuid"),
    src: taskValue(card, "src"),
    dst: taskValue(card, "dst"),
    cron: taskValue(card, "cron"),
    mounted_path: taskValue(card, "mounted_path"),
  }));
  result.alist2strm_tasks = [...document.querySelectorAll("#config-strm-tasks .config-task-item")].map((card) => ({
    ...(card.configOriginal || {}),
    uuid: taskValue(card, "uuid"),
    source_dir: taskValue(card, "source_dir"),
    target_dir: taskValue(card, "target_dir"),
    cron: taskValue(card, "cron"),
    mode: taskValue(card, "mode"),
    flatten_mode: taskValue(card, "flatten_mode"),
    subtitle: taskValue(card, "subtitle"),
    image: taskValue(card, "image"),
    nfo: taskValue(card, "nfo"),
    overwrite: taskValue(card, "overwrite"),
    other_extensions: taskValue(card, "other_extensions").split(",").map((value) => value.trim()).filter(Boolean),
    max_workers: taskValue(card, "max_workers"),
    max_downloaders: taskValue(card, "max_downloaders"),
  }));
  result.cover_dst_when_diff = document.getElementById("config-cover-dst").checked;
  result.delete_src_when_same = document.getElementById("config-delete-src").checked;
  result.dir_tree_build_tasks = [...document.querySelectorAll("#config-tree-tasks .config-task-item")].map((card) => ({
    ...(card.configOriginal || {}),
    uuid: taskValue(card, "uuid"),
    src: taskValue(card, "src"),
    cron: taskValue(card, "cron"),
    qps: taskValue(card, "qps"),
    run_at_start: taskValue(card, "run_at_start"),
  }));
  result.emby = {
    ...(result.emby || {}),
    enabled: document.getElementById("config-emby-enabled").checked,
    url: document.getElementById("config-emby-url").value.trim(),
    apikey: document.getElementById("config-emby-apikey").value,
    mount_path: document.getElementById("config-emby-mount").value.trim(),
  };
  result.webhook = {
    ...(result.webhook || {}),
    enabled: document.getElementById("config-webhook-enabled").checked,
    url: document.getElementById("config-webhook-url").value.trim(),
  };
  return result;
}

function updateConfigPreview() {
  if (!loadedConfig) return;
  document.getElementById("config-editor").value = JSON.stringify(collectConfigForm(), null, 2);
}

function updateIntegrationRequirements() {
  const embyEnabled = document.getElementById("config-emby-enabled").checked;
  const webhookEnabled = document.getElementById("config-webhook-enabled").checked;
  document.getElementById("config-emby-module").classList.toggle("config-module-disabled", !embyEnabled);
  document.getElementById("config-webhook-module").classList.toggle("config-module-disabled", !webhookEnabled);
  document.getElementById("config-emby-url").required = embyEnabled;
  document.getElementById("config-emby-apikey").required = embyEnabled;
  document.getElementById("config-webhook-url").required = webhookEnabled;
}

function renderConfigForm(value) {
  loadedConfig = cloneConfig(value);
  const alist = value.alist || {};
  setConfigValue("config-alist-url", alist.url);
  setConfigValue("config-alist-apikey", alist.apikey);
  setConfigValue("config-task-timeout", alist.task_missing_timeout_seconds ?? 600);
  setConfigValue("config-request-timeout", alist.request_timeout_seconds ?? 15);
  setConfigValue("config-health-interval", alist.healthcheck_interval_seconds ?? 15);
  setConfigValue("config-health-timeout", alist.healthcheck_timeout_seconds ?? 3);
  document.getElementById("config-cover-dst").checked = Boolean(value.cover_dst_when_diff);
  document.getElementById("config-delete-src").checked = Boolean(value.delete_src_when_same);

  const syncContainer = document.getElementById("config-sync-tasks");
  syncContainer.replaceChildren();
  (value.tasks || []).forEach(appendSyncTask);
  const strmContainer = document.getElementById("config-strm-tasks");
  strmContainer.replaceChildren();
  (value.alist2strm_tasks || []).forEach(appendStrmTask);
  const treeContainer = document.getElementById("config-tree-tasks");
  treeContainer.replaceChildren();
  (value.dir_tree_build_tasks || []).forEach(appendTreeTask);

  const emby = value.emby || {};
  document.getElementById("config-emby-enabled").checked = Boolean(emby.enabled);
  setConfigValue("config-emby-url", emby.url);
  setConfigValue("config-emby-apikey", emby.apikey);
  setConfigValue("config-emby-mount", emby.mount_path);
  const webhook = value.webhook || {};
  document.getElementById("config-webhook-enabled").checked = Boolean(webhook.enabled);
  setConfigValue("config-webhook-url", webhook.url);
  document.querySelectorAll("[data-reveal]").forEach((button) => {
    document.getElementById(button.dataset.reveal).type = "password";
    button.textContent = "显示";
  });
  updateIntegrationRequirements();
  updateConfigPreview();
}

function markConfigDirty() {
  if (!configLoaded) return;
  configDirty = true;
  const state = document.getElementById("config-state");
  state.textContent = "有未保存修改";
  state.classList.add("dirty");
  updateConfigPreview();
}

async function loadConfig(force = false) {
  if (configDirty && !force) return;
  const state = document.getElementById("config-state");
  state.textContent = "正在载入";
  try {
    const data = await getJson("/ui/api/config");
    renderConfigForm(data.config);
    document.getElementById("config-path").textContent = `配置路径：${data.path}`;
    configWritable = data.writable;
    document.getElementById("config-save").disabled = !configWritable;
    state.textContent = configWritable ? "已载入" : "配置文件不可写";
    state.classList.remove("dirty");
    configLoaded = true;
    configDirty = false;
  } catch (error) {
    state.textContent = `载入失败：${error.message}`;
    showToast(`配置载入失败：${error.message}`);
  }
}

async function saveConfig() {
  const form = document.getElementById("config-form");
  const button = document.getElementById("config-save");
  if (!form.reportValidity()) {
    showToast("请先填写所有必填字段，并检查数字和 URL 格式");
    return;
  }
  const parsed = collectConfigForm();

  button.disabled = true;
  button.textContent = "保存中";
  try {
    const response = await fetch("/ui/api/config", {
      method: "PUT",
      headers: writeHeaders({ "Content-Type": "application/json", Accept: "application/json" }),
      body: JSON.stringify(parsed),
    });
    handleAuthentication(response);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || `${response.status} ${response.statusText}`);
    renderConfigForm(data.config);
    configDirty = false;
    document.getElementById("config-state").textContent = "已保存并应用";
    document.getElementById("config-state").classList.remove("dirty");
    showToast(data.alist?.online ? "配置已保存并生效" : "配置已保存；AList 当前不可用，业务任务保持暂停", data.alist?.online ? "success" : "error");
    await refreshAll();
  } catch (error) {
    showToast(`保存失败：${error.message}`);
  } finally {
    button.disabled = !configWritable;
    button.textContent = "保存并应用";
  }
}

async function recheckAlist() {
  const button = document.getElementById("alist-recheck");
  button.disabled = true;
  button.textContent = "检测中";
  try {
    const response = await fetch("/ui/api/alist/recheck", { method: "POST", headers: writeHeaders({ Accept: "application/json" }) });
    handleAuthentication(response);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || `${response.status} ${response.statusText}`);
    showToast(data.alist?.online ? `AList 在线，响应 ${data.alist.latency_ms} ms` : `AList 不可用：${data.alist?.error || "未知错误"}`, data.alist?.online ? "success" : "error");
    await refreshAll();
  } catch (error) {
    showToast(`检测失败：${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "检测 AList";
  }
}


async function runAlist2Strm(task, button) {
  if (!window.confirm(`确认立即生成 STRM？\n${task.parameters?.source_dir || task.task_uuid}`)) return;

  button.disabled = true;
  button.textContent = "提交中";
  try {
    const response = await fetch(
      `/ui/api/tasks/alist2strm/${encodeURIComponent(task.task_uuid)}/run`,
      { method: "POST", headers: writeHeaders({ Accept: "application/json" }) },
    );
    handleAuthentication(response);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || `${response.status} ${response.statusText}`);

    const status = data.run?.status;
    if (status === "skipped_busy") {
      showToast("该 STRM 实例仍在执行，本次触发已跳过");
    } else if (status === "skipped_unavailable") {
      showToast("AList 当前不可用，本次触发已跳过");
    } else {
      showToast(`STRM 生成已加入独立队列 · ${task.task_uuid}`, "success");
    }
    await refreshAll();
  } catch (error) {
    showToast(`STRM 任务触发失败：${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "立即生成";
  }
}

async function runDirTreeBuild(task, button) {
  if (!window.confirm(`确认立即重建目录树？\n${task.parameters?.src || task.task_uuid}`)) return;

  button.disabled = true;
  button.textContent = "提交中";
  try {
    const response = await fetch(
      `/ui/api/tasks/dir-tree-build/${encodeURIComponent(task.task_uuid)}/run`,
      { method: "POST", headers: writeHeaders({ Accept: "application/json" }) },
    );
    handleAuthentication(response);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || `${response.status} ${response.statusText}`);

    const status = data.run?.status;
    if (status === "skipped_busy") {
      showToast("该实例仍有任务未完成，本次手动触发已跳过");
    } else if (status === "skipped_unavailable") {
      showToast("AList 当前不可用，本次手动触发已跳过");
    } else {
      showToast(`目录树重建已加入队列 · ${task.task_uuid}`, "success");
    }
    await refreshAll();
  } catch (error) {
    showToast(`目录树重建触发失败：${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "立即重建";
  }
}

function renderTasks(data) {
  const grid = document.getElementById("task-grid");
  grid.replaceChildren();
  (data.tasks || []).forEach((task) => {
    const card = el("article", "task-card");
    const head = el("div", "task-card-head");
    const title = el("div");
    title.append(el("span", "task-type", label(task.task_type)), el("h3", "", task.name));
    head.append(title, el("code", "task-schedule", task.schedule));
    card.append(head);

    const params = el("div", "task-params");
    const entries = Object.entries(task.parameters || {});
    if (!entries.length) entries.push(["说明", "每分钟检查 AList 已完成复制"]);
    entries.forEach(([key, value]) => {
      const row = el("div", "param-row");
      row.append(el("span", "", key), el("code", "", typeof value === "object" ? JSON.stringify(value) : value));
      params.append(row);
    });
    card.append(params);

    const footer = el("div", "task-footer");
    const next = el("span", "", `下次：${formatDate(task.next_run_time)}`);
    const latest = task.last_run ? statusNode(task.last_run.status) : statusNode("");
    if (!task.last_run) latest.textContent = "尚未运行";
    const footerMeta = el("div", "task-footer-meta");
    footerMeta.append(next, latest);
    footer.append(footerMeta);
    if (task.task_type === "dir_tree_build") {
      const runButton = el("button", "task-run-button", "立即重建");
      runButton.type = "button";
      runButton.addEventListener("click", (event) => {
        event.stopPropagation();
        runDirTreeBuild(task, runButton);
      });
      footer.append(runButton);
    }
    if (task.task_type === "alist2strm") {
      const runButton = el("button", "task-run-button", "立即生成");
      runButton.type = "button";
      runButton.addEventListener("click", (event) => {
        event.stopPropagation();
        runAlist2Strm(task, runButton);
      });
      footer.append(runButton);
    }
    card.append(footer);
    card.addEventListener("click", () => showDetail(`任务详情 · ${task.task_uuid}`, task));
    grid.append(card);
  });
}

function renderRequests(data) {
  renderTable(
    "requests-table",
    [
      { title: "入口", render: (item) => {
        const wrap = el("div");
        wrap.append(el("span", "primary-cell", item.route), el("span", "secondary", item.method));
        return wrap;
      } },
      { title: "任务", render: (item) => item.task_uuid || "—" },
      { title: "HTTP", render: (item) => item.status_code || "处理中" },
      { title: "产生运行", render: (item) => item.run_count },
      { title: "接收时间", render: (item) => formatDate(item.received_at) },
    ],
    data.requests || [],
    (item) => `API 请求 · ${item.route}`,
  );
}

function renderCallbacks(data) {
  renderTable(
    "callbacks-table",
    [
      { title: "服务", render: (item) => {
        const wrap = el("div");
        wrap.append(el("span", "primary-cell", item.service), el("span", "secondary", item.target || "—"));
        return wrap;
      } },
      { title: "状态", render: (item) => statusNode(item.status) },
      { title: "HTTP", render: (item) => item.status_code || "—" },
      { title: "耗时", render: (item) => item.duration_ms == null ? "—" : `${item.duration_ms} ms` },
      { title: "调用时间", render: (item) => formatDate(item.created_at) },
    ],
    data.callbacks || [],
    (item) => `回调详情 · ${item.service}`,
  );
}

function selectedTimeStart(value) {
  const now = new Date();
  if (value === "today") {
    return new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
  }
  const durations = {
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
    "30d": 30 * 24 * 60 * 60 * 1000,
  };
  return durations[value] ? new Date(now.getTime() - durations[value]).toISOString() : "";
}

async function refreshAll() {
  if (refreshing) return;
  refreshing = true;
  const button = document.getElementById("refresh-button");
  button.disabled = true;
  button.textContent = "刷新中";
  try {
    const statusFilter = document.getElementById("run-status-filter").value;
    const instanceFilter = parseInstanceKey(
      document.getElementById("run-instance-filter").value,
    );
    const timeStart = selectedTimeStart(document.getElementById("run-time-filter").value);
    const runQuery = new URLSearchParams({ limit: "100" });
    if (statusFilter) runQuery.set("status", statusFilter);
    if (instanceFilter) {
      runQuery.set("task_type", instanceFilter[0]);
      runQuery.set("task_uuid", instanceFilter[1]);
    }
    if (timeStart) runQuery.set("created_from", timeStart);
    const [overview, tasks, runs, requests, callbacks] = await Promise.all([
      getJson("/ui/api/overview"),
      getJson("/ui/api/tasks"),
      getJson(`/ui/api/runs?${runQuery.toString()}`),
      getJson("/ui/api/requests?limit=100"),
      getJson("/ui/api/callbacks?limit=100"),
    ]);
    populateRunInstanceFilter(tasks.tasks, overview.recent_runs);
    renderOverview(overview);
    renderTasks(tasks);
    renderRunsTable("runs-table", runs.runs || []);
    renderRequests(requests);
    renderCallbacks(callbacks);
    document.getElementById("updated-at").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN")}`;
  } catch (error) {
    document.querySelector(".live-dot").className = "live-dot error";
    document.getElementById("sidebar-service-state").textContent = "连接失败";
    showToast(`刷新失败：${error.message}`);
  } finally {
    refreshing = false;
    button.disabled = false;
    button.textContent = "立即刷新";
  }
}

function switchView(view) {
  if (!viewMeta[view]) return;
  currentView = view;
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  document.querySelectorAll(".view").forEach((item) => item.classList.toggle("active", item.id === `view-${view}`));
  document.getElementById("view-eyebrow").textContent = viewMeta[view][0];
  document.getElementById("view-title").textContent = viewMeta[view][1];
  window.history.replaceState(null, "", `#${view}`);
  if (view === "config" && !configLoaded) loadConfig();
}

document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
document.querySelectorAll("[data-jump]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.jump)));
document.getElementById("refresh-button").addEventListener("click", refreshAll);
document.getElementById("run-status-filter").addEventListener("change", refreshAll);
document.getElementById("run-instance-filter").addEventListener("change", refreshAll);
document.getElementById("run-time-filter").addEventListener("change", refreshAll);
document.getElementById("config-form").addEventListener("submit", (event) => {
  event.preventDefault();
  saveConfig();
});
document.getElementById("config-form").addEventListener("input", markConfigDirty);
document.getElementById("config-form").addEventListener("change", (event) => {
  if (["config-emby-enabled", "config-webhook-enabled"].includes(event.target.id)) {
    updateIntegrationRequirements();
  }
  markConfigDirty();
});
document.getElementById("config-add-sync").addEventListener("click", () => {
  appendSyncTask({ uuid: `sync-${Date.now().toString(36)}`, cron: "0 * * * *" });
  markConfigDirty();
});
document.getElementById("config-add-strm").addEventListener("click", () => {
  appendStrmTask({
    uuid: `strm-${Date.now().toString(36)}`,
    cron: "0 */6 * * *",
    mode: "alist_url",
    max_workers: 20,
    max_downloaders: 3,
  });
  markConfigDirty();
});
document.getElementById("config-add-tree").addEventListener("click", () => {
  appendTreeTask({ uuid: `refresh-${Date.now().toString(36)}`, cron: "0 12 * * *", qps: 0.1 });
  markConfigDirty();
});
document.querySelectorAll("[data-reveal]").forEach((button) => {
  button.addEventListener("click", () => {
    const input = document.getElementById(button.dataset.reveal);
    const reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    button.textContent = reveal ? "隐藏" : "显示";
  });
});
document.getElementById("config-reload").addEventListener("click", () => {
  if (configDirty && !window.confirm("放弃尚未保存的配置修改并重新载入？")) return;
  loadConfig(true);
});
document.getElementById("config-save").addEventListener("click", saveConfig);
document.getElementById("alist-recheck").addEventListener("click", recheckAlist);
document.getElementById("dialog-close").addEventListener("click", () => document.getElementById("detail-dialog").close());
document.getElementById("detail-dialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});

const initialView = window.location.hash.slice(1);
if (viewMeta[initialView]) switchView(initialView);
refreshAll();
window.setInterval(refreshAll, 5000);
