import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Bot, Clock3, Plus, RefreshCw, UserRound } from "lucide-react";

import { Glass } from "../../components/ui/Glass";
import { ActionButton, AsyncNotice, PageShell } from "../../components/ui/Workbench";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { AgentLifecycleBadge } from "./AgentLifecycleBadge";
import { CreateAgentDrawer } from "./CreateAgentDrawer";
import type { AgentCenterApi, AgentListPage, AgentSummary, CreateAgentInput, ManageableDepartment } from "./types";

const PAGE_SIZE = 12;

function formatUpdatedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function AgentCard({ agent, onOpen }: { agent: AgentSummary; onOpen: (agent: AgentSummary) => void }) {
  const productionVersion = agent.publishedVersion === null ? "尚未发布" : `生产 v${agent.publishedVersion}`;

  return (
    <button
      className="group min-w-0 rounded-panel border border-line bg-black/10 p-4 text-left transition duration-200 hover:-translate-y-0.5 hover:border-accent-cyan/28 hover:bg-accent-cyan/5 focus-visible:border-accent-cyan/35"
      type="button"
      aria-label={`打开 Agent：${agent.name}`}
      onClick={() => onOpen(agent)}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-xl border border-accent-cyan/18 bg-accent-cyan/8 text-accent-cyan">
          <Bot size={17} />
        </span>
        <div className="flex items-center gap-2">
          <AgentLifecycleBadge status={agent.lifecycleStatus} />
          <ArrowRight className="text-faint transition group-hover:translate-x-0.5 group-hover:text-accent-cyan" size={14} />
        </div>
      </div>

      <h2 className="mt-3 truncate text-[13px] font-semibold text-ink">{agent.name}</h2>
      <p className="mt-1.5 min-h-10 line-clamp-2 text-[9px] leading-5 text-muted">{agent.description || "暂无用途说明"}</p>

      <div className="mt-3 flex items-center justify-between gap-3 border-t border-line/60 pt-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 font-mono text-[8px] text-accent-green">{productionVersion}</span>
          <span className="shrink-0 font-mono text-[8px] text-accent-violet">修订 r{agent.aggregateRevision}</span>
        </div>
        <span className="truncate text-[8px] text-faint">{agent.ownerDepartmentId}</span>
      </div>
      {agent.hasUnpublishedChanges ? (
        <p className="mt-2 rounded-md border border-accent-amber/18 bg-accent-amber/7 px-2 py-1.5 text-[8px] text-accent-amber">有未发布的草稿修改</p>
      ) : null}
      <dl className="mt-2 grid gap-1.5 text-[8px] text-faint">
        <div className="flex min-w-0 items-center gap-1.5">
          <UserRound size={10} />
          <dt className="sr-only">负责人</dt>
          <dd className="truncate">负责人 · {agent.createdBy}</dd>
        </div>
        <div className="flex min-w-0 items-center gap-1.5">
          <Clock3 size={10} />
          <dt className="sr-only">更新时间</dt>
          <dd className="truncate">更新于 · {formatUpdatedAt(agent.updatedAt)}</dd>
        </div>
      </dl>
    </button>
  );
}

type AgentCenterPageProps = {
  api: AgentCenterApi;
  onOpenAgent: (agent: AgentSummary) => void;
};

export function AgentCenterPage({ api, onOpenAgent }: AgentCenterPageProps) {
  const [page, setPage] = useState(1);
  const [agents, setAgents] = useState<AgentListPage>();
  const [listPending, setListPending] = useState(true);
  const [listError, setListError] = useState<string>();
  const [reloadKey, setReloadKey] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [departments, setDepartments] = useState<ManageableDepartment[]>([]);
  const [departmentsPending, setDepartmentsPending] = useState(false);
  const [departmentsError, setDepartmentsError] = useState<string>();
  const [departmentsReloadKey, setDepartmentsReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setListPending(true);
    setListError(undefined);

    api.listAgents({ page, pageSize: PAGE_SIZE }, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setAgents(result);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setListError(error instanceof Error ? error.message : "Agent 列表加载失败");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setListPending(false);
      });

    return () => controller.abort();
  }, [api, page, reloadKey]);

  useEffect(() => {
    if (!createOpen) return;

    const controller = new AbortController();
    setDepartments([]);
    setDepartmentsPending(true);
    setDepartmentsError(undefined);

    api.listManageableDepartments(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setDepartments(result);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setDepartments([]);
          setDepartmentsError(error instanceof Error ? error.message : "责任部门加载失败");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setDepartmentsPending(false);
      });

    return () => controller.abort();
  }, [api, createOpen, departmentsReloadKey]);

  const createRequest = useCallback(
    (input: CreateAgentInput, signal: AbortSignal) => api.createAgent(input, signal),
    [api],
  );
  const createAction = useAsyncAction(createRequest);

  const createAgent = async (input: CreateAgentInput) => {
    const created = await createAction.run(input);
    if (!created) return;

    setCreateOpen(false);
    setReloadKey((current) => current + 1);
    onOpenAgent(created);
  };

  const openCreateDrawer = () => {
    createAction.reset();
    setCreateOpen(true);
  };

  const closeCreateDrawer = () => {
    createAction.reset();
    setCreateOpen(false);
  };

  const hasPreviousPage = page > 1;
  const hasNextPage = Boolean(agents && agents.page * agents.pageSize < agents.total);

  return (
    <>
      <PageShell
        eyebrow="AGENT STUDIO"
        title="Agent 中心"
        description="创建、查看并进入当前身份有权使用的 Agent；编排始终绑定具体 Agent 草稿。"
        actions={<ActionButton onClick={openCreateDrawer}><Plus size={14} />新建 Agent</ActionButton>}
      >
        {listPending ? <AsyncNotice pending /> : null}

        {!listPending && listError ? (
          <Glass className="rounded-panel p-4">
            <AsyncNotice error={listError} />
            <div className="mt-3 flex justify-center">
              <ActionButton variant="secondary" onClick={() => setReloadKey((current) => current + 1)}>
                <RefreshCw size={13} />重新加载
              </ActionButton>
            </div>
          </Glass>
        ) : null}

        {!listPending && !listError && agents?.items.length === 0 ? (
          <Glass className="grid min-h-80 place-items-center rounded-panel p-8 text-center">
            <div className="max-w-sm">
              <span className="mx-auto grid size-14 place-items-center rounded-2xl border border-accent-cyan/18 bg-accent-cyan/8 text-accent-cyan">
                <Bot size={25} />
              </span>
              <h2 className="mt-4 text-base font-semibold">还没有 Agent</h2>
              <p className="mt-2 text-[10px] leading-5 text-muted">创建第一个 Agent 草稿后，系统会带你进入绑定该 Agent 的编排空间。</p>
              <ActionButton className="mt-5" onClick={openCreateDrawer}><Plus size={14} />新建 Agent</ActionButton>
            </div>
          </Glass>
        ) : null}

        {!listPending && !listError && agents && agents.items.length > 0 ? (
          <>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
              {agents.items.map((agent) => <AgentCard key={agent.id} agent={agent} onOpen={onOpenAgent} />)}
            </div>

            <div className="flex items-center justify-between gap-3 rounded-xl border border-line bg-black/8 px-3 py-2">
              <span className="text-[9px] text-muted">共 {agents.total} 个 Agent · 第 {agents.page} 页</span>
              <div className="flex gap-2">
                <ActionButton variant="secondary" disabled={!hasPreviousPage} onClick={() => setPage((current) => current - 1)}>
                  <ArrowLeft size={13} />上一页
                </ActionButton>
                <ActionButton variant="secondary" disabled={!hasNextPage} onClick={() => setPage((current) => current + 1)}>
                  下一页<ArrowRight size={13} />
                </ActionButton>
              </div>
            </div>
          </>
        ) : null}
      </PageShell>

      <CreateAgentDrawer
        open={createOpen}
        pending={createAction.pending}
        error={createAction.error}
        departments={departments}
        departmentsPending={departmentsPending}
        departmentsError={departmentsError}
        onClose={closeCreateDrawer}
        onRetryDepartments={() => setDepartmentsReloadKey((current) => current + 1)}
        onSubmit={createAgent}
      />
    </>
  );
}
