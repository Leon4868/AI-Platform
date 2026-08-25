import { useState, type FormEvent } from "react";
import { FilePenLine, RotateCcw, Sparkles } from "lucide-react";

import { ActionButton, AsyncNotice, FormField, KeyValue, PageShell, SectionCard, StatusBadge } from "../../components/ui/Workbench";
import { isBrowserDownloadUri } from "../enterprise-api/download";
import type { EnterpriseApi } from "../enterprise-api/types";
import { useKnowledgeBases } from "../enterprise-api/useKnowledgeBases";
import { useDocumentTask } from "./useDocumentTask";

export function DocumentPage({ api, pollIntervalMs = 900 }: { api: EnterpriseApi; pollIntervalMs?: number }) {
  const bases = useKnowledgeBases(api);
  const run = useDocumentTask(api, pollIntervalMs);
  const [workflowDefinitionId, setWorkflowDefinitionId] = useState("");
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [logicalModelCode, setLogicalModelCode] = useState("enterprise-doc-main");
  const [selectedBases, setSelectedBases] = useState<string[]>([]);
  const [validationError, setValidationError] = useState<string>();

  const toggleBase = (id: string) => setSelectedBases((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!workflowDefinitionId.trim() || !title.trim() || !instructions.trim()) {
      setValidationError("请填写 Workflow ID、标题和生成指令");
      return;
    }
    setValidationError(undefined);
    await run.start({
      title: title.trim(), workflowDefinitionId: workflowDefinitionId.trim(), knowledgeBaseIds: selectedBases,
      logicalModelCode: logicalModelCode.trim(), instructions: instructions.trim(),
      sources: [{ kind: "user_input", label: "员工生成指令" }], outputFormat: "markdown",
    });
  };

  const downloadable = isBrowserDownloadUri(run.asset?.storageUri);

  return (
    <PageShell eyebrow="DOCUMENT PRODUCTION" title="文档生产工作台" description="组合企业知识与受控 Workflow，生成可追溯、可审计的 Markdown 草稿资产。" transportKind={api.kind} actions={run.task ? <StatusBadge status={run.task.status} /> : undefined}>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(330px,.85fr)]">
        <SectionCard title="创建文档任务" description="一期仅开放 Markdown；DOCX/PDF 将在后续转换管线提供" action={<FilePenLine size={17} className="text-accent-cyan" />}>
          <form className="grid gap-3" onSubmit={onSubmit}>
            <FormField label="Workflow Definition ID" htmlFor="document-workflow-id" hint="必须是当前租户内已发布或可执行的 Workflow UUID"><input id="document-workflow-id" className="form-control font-mono" value={workflowDefinitionId} onChange={(e) => setWorkflowDefinitionId(e.target.value)} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" required /></FormField>
            <div className="grid gap-3 sm:grid-cols-2">
              <FormField label="文档标题" htmlFor="document-title"><input id="document-title" className="form-control" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：产品周报" required /></FormField>
              <FormField label="逻辑模型" htmlFor="document-model"><input id="document-model" className="form-control font-mono" value={logicalModelCode} onChange={(e) => setLogicalModelCode(e.target.value)} required /></FormField>
            </div>
            <FormField label="企业知识库" hint="可多选；生成任务会先检索并固化 citation">
              <div className="grid gap-2 rounded-xl border border-line bg-black/8 p-2.5 sm:grid-cols-2">
                <AsyncNotice pending={bases.pending} error={bases.error} empty={!bases.pending && !bases.error && bases.items.length === 0} emptyText="暂无知识库，也可以仅使用员工输入生成" />
                {bases.items.map((item) => <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-line px-2.5 py-2 text-[9px] text-muted hover:bg-accent-cyan/5" key={item.id}><input type="checkbox" checked={selectedBases.includes(item.id)} onChange={() => toggleBase(item.id)} /><span>{item.name}</span></label>)}
              </div>
            </FormField>
            <FormField label="生成指令" htmlFor="document-instructions"><textarea id="document-instructions" className="form-control min-h-36 resize-y" value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder="说明受众、结构、重点、语气与必须引用的事实…" required /></FormField>
            <AsyncNotice pending={run.pending} error={validationError ?? run.error} />
            {run.task?.status === "failed" ? <AsyncNotice error={run.task.error?.message ?? "文档任务执行失败，未生成可发布资产；可修改输入后新建任务。"} /> : null}
            {run.task?.status === "cancelled" ? <AsyncNotice empty emptyText="文档任务已取消，未生成草稿资产。" /> : null}
            <div className="flex flex-wrap gap-2">
              <ActionButton type="submit" disabled={run.pending}><Sparkles size={14} />生成 Markdown 草稿</ActionButton>
              {run.task ? <ActionButton variant="secondary" onClick={run.reset}><RotateCcw size={13} />新建任务</ActionButton> : null}
            </div>
          </form>
        </SectionCard>

        <div className="grid content-start gap-4">
          <SectionCard title="运行与追踪" description="任务状态由服务端轮询，不在前端伪造成功">
            {!run.task ? <AsyncNotice empty emptyText="提交任务后显示 Run、Trace 与终态" /> : <>
              <KeyValue label="任务状态" value={<StatusBadge status={run.task.status} />} />
              <KeyValue label="Task ID" value={run.task.taskId} mono />
              <KeyValue label="Workflow Run" value={run.task.workflowRunId} mono />
              <KeyValue label="Trace" value={run.task.traceId} mono />
              <KeyValue label="引用数量" value={run.task.citations.length} />
              <KeyValue label="Draft Asset" value={run.task.draftAssetId} mono />
            </>}
          </SectionCard>
          <SectionCard title="草稿企业资产" description="草稿不会自动发布；下载地址在读取资产时重新授权">
            {run.asset ? <>
              <div className="mb-3 flex items-center justify-between gap-2"><strong className="text-[11px]">{run.asset.name}</strong><StatusBadge status={run.asset.status} /></div>
              <KeyValue label="Asset ID" value={run.asset.id} mono />
              <KeyValue label="MIME" value={run.asset.mimeType} />
              <KeyValue label="内容哈希" value={run.asset.contentHash} mono />
              <KeyValue label="血缘来源" value={run.asset.lineage.length} />
              {downloadable ? <a className="action-button action-button-primary mt-3 w-full" href={run.asset.storageUri} target="_blank" rel="noreferrer">下载草稿</a> : <div className="mt-3"><AsyncNotice empty emptyText={run.asset.storageUri ? "当前存储后端不支持浏览器下载" : "资产暂无下载地址"} /></div>}
            </> : <AsyncNotice pending={run.pending} error={run.assetError} empty={!run.pending && !run.assetError} emptyText="任务成功后展示草稿资产" />}
          </SectionCard>
        </div>
      </div>
    </PageShell>
  );
}
