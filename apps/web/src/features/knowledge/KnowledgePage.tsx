import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Database, FileUp, Plus, Search } from "lucide-react";

import { ActionButton, AsyncNotice, FormField, PageShell, SectionCard, StatusBadge } from "../../components/ui/Workbench";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { cn } from "../../lib/cn";
import type { CreateKnowledgeBaseInput, DataScope, EnterpriseApi, SecurityLevel } from "../enterprise-api/types";
import { useKnowledgeBases } from "../enterprise-api/useKnowledgeBases";

const initialCreate: CreateKnowledgeBaseInput = {
  name: "", description: "", ownerDepartmentId: "dept-platform", securityLevel: "internal", embeddingModelCode: "embedding-main",
};

const supportedExtensions = new Set(["txt", "md", "html", "htm"]);

export function KnowledgePage({ api }: { api: EnterpriseApi }) {
  const bases = useKnowledgeBases(api);
  const [selectedId, setSelectedId] = useState("");
  const [createForm, setCreateForm] = useState(initialCreate);
  const [file, setFile] = useState<File | null>(null);
  const [dataScope, setDataScope] = useState<DataScope>("department");
  const [projectId, setProjectId] = useState("");
  const [securityLevel, setSecurityLevel] = useState<SecurityLevel>("internal");
  const [query, setQuery] = useState("");
  const [uploadValidationError, setUploadValidationError] = useState<string>();
  const [searchValidationError, setSearchValidationError] = useState<string>();

  useEffect(() => {
    if (!selectedId && bases.items[0]) setSelectedId(bases.items[0].id);
  }, [bases.items, selectedId]);

  const createRequest = useCallback((input: CreateKnowledgeBaseInput, signal: AbortSignal) => api.createKnowledgeBase(input, signal), [api]);
  const createAction = useAsyncAction(createRequest);
  const uploadRequest = useCallback((input: { knowledgeBaseId: string; file: File; dataScope: DataScope; securityLevel: SecurityLevel; projectId?: string }, signal: AbortSignal) => api.uploadKnowledgeDocument(input.knowledgeBaseId, input.file, input.dataScope, input.securityLevel, signal, input.projectId), [api]);
  const uploadAction = useAsyncAction(uploadRequest);
  const searchRequest = useCallback((input: { knowledgeBaseId: string; query: string }, signal: AbortSignal) => api.searchKnowledge(input.knowledgeBaseId, input.query, 8, signal), [api]);
  const searchAction = useAsyncAction(searchRequest);

  const onCreate = async (event: FormEvent) => {
    event.preventDefault();
    const created = await createAction.run(createForm);
    if (created) {
      setCreateForm(initialCreate);
      setSelectedId(created.id);
      await bases.refresh();
    }
  };

  const onUpload = async (event: FormEvent) => {
    event.preventDefault();
    setUploadValidationError(undefined);
    if (!selectedId || !file) { setUploadValidationError("请选择知识库和文件"); return; }
    if (dataScope === "project" && !projectId.trim()) { setUploadValidationError("项目范围必须填写 Project ID"); return; }
    const extension = file.name.toLowerCase().split(".").pop() ?? "";
    if (!supportedExtensions.has(extension)) { setUploadValidationError("一期仅支持 TXT、Markdown 和 HTML"); return; }
    await uploadAction.run({ knowledgeBaseId: selectedId, file, dataScope, securityLevel, projectId: dataScope === "project" ? projectId.trim() : undefined });
  };

  const onSearch = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedId || !query.trim()) { setSearchValidationError("请选择知识库并输入检索内容"); return; }
    setSearchValidationError(undefined);
    await searchAction.run({ knowledgeBaseId: selectedId, query: query.trim() });
  };

  return (
    <PageShell eyebrow="ENTERPRISE KNOWLEDGE" title="企业知识库" description="沉淀企业文档，按身份权限检索可追溯的引用片段。" transportKind={api.kind}>
      <div className="grid gap-4 xl:grid-cols-[minmax(280px,.72fr)_minmax(0,1.28fr)]">
        <div className="grid content-start gap-4">
          <SectionCard title="知识空间" description="选择本次上传与检索使用的知识库" action={<Database size={16} className="text-accent-cyan" />}>
            <AsyncNotice pending={bases.pending} error={bases.error} empty={!bases.pending && !bases.error && bases.items.length === 0} emptyText="还没有知识库，请先创建" />
            <div className="grid gap-2">
              {bases.items.map((item) => (
                <button className={cn("rounded-xl border p-3 text-left transition hover:border-line-strong hover:bg-accent-cyan/5", item.id === selectedId ? "border-accent-cyan/35 bg-accent-cyan/8" : "border-line bg-black/8")} type="button" key={item.id} onClick={() => setSelectedId(item.id)} aria-pressed={item.id === selectedId}>
                  <div className="flex items-center justify-between gap-2"><strong className="text-[11px]">{item.name}</strong><StatusBadge status={item.securityLevel} /></div>
                  <p className="mt-1.5 line-clamp-2 text-[9px] leading-4 text-muted">{item.description || "暂无描述"}</p>
                  <small className="mt-2 block font-mono text-[8px] text-faint">{item.ownerDepartmentId} · {item.embeddingModelCode}</small>
                </button>
              ))}
            </div>
          </SectionCard>
          <SectionCard title="新建知识库" description="创建后自动加入当前员工可见范围">
            <form className="grid gap-3" onSubmit={onCreate}>
              <FormField label="名称" htmlFor="knowledge-name"><input id="knowledge-name" className="form-control" value={createForm.name} onChange={(e) => setCreateForm((v) => ({ ...v, name: e.target.value }))} required placeholder="例如：产品制度库" /></FormField>
              <FormField label="描述" htmlFor="knowledge-description"><textarea id="knowledge-description" className="form-control min-h-19 resize-y" value={createForm.description} onChange={(e) => setCreateForm((v) => ({ ...v, description: e.target.value }))} placeholder="说明该知识库的内容范围" /></FormField>
              <div className="grid gap-3 sm:grid-cols-2">
                <FormField label="归属部门" htmlFor="knowledge-owner"><input id="knowledge-owner" className="form-control" value={createForm.ownerDepartmentId} onChange={(e) => setCreateForm((v) => ({ ...v, ownerDepartmentId: e.target.value }))} required /></FormField>
                <FormField label="安全级别" htmlFor="knowledge-security"><select id="knowledge-security" className="form-control" value={createForm.securityLevel} onChange={(e) => setCreateForm((v) => ({ ...v, securityLevel: e.target.value as SecurityLevel }))}><option value="internal">内部</option><option value="department_sensitive">部门敏感</option><option value="confidential">机密</option></select></FormField>
              </div>
              <FormField label="Embedding 模型" htmlFor="knowledge-embedding"><input id="knowledge-embedding" className="form-control" value={createForm.embeddingModelCode} onChange={(e) => setCreateForm((v) => ({ ...v, embeddingModelCode: e.target.value }))} required /></FormField>
              <AsyncNotice pending={createAction.pending} error={createAction.error} />
              <ActionButton type="submit" disabled={createAction.pending}><Plus size={14} />创建知识库</ActionButton>
            </form>
          </SectionCard>
        </div>
        <div className="grid content-start gap-4">
          <SectionCard title="上传并索引" description="一期支持 TXT、Markdown、HTML，源文件同时沉淀为企业资产" action={<FileUp size={16} className="text-accent-blue" />}>
            <form className="grid gap-3" onSubmit={onUpload}>
              <FormField label="目标知识库" htmlFor="upload-knowledge-base"><select id="upload-knowledge-base" className="form-control" value={selectedId} onChange={(e) => setSelectedId(e.target.value)} required><option value="">请选择</option>{bases.items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></FormField>
              <FormField label="知识文件" htmlFor="knowledge-file" hint="最大 50 MiB；不支持的格式会被明确拒绝"><input id="knowledge-file" className="form-control file:mr-3 file:rounded-md file:border-0 file:bg-accent-cyan/12 file:px-2 file:py-1 file:text-[9px] file:text-accent-cyan" type="file" accept=".txt,.md,.markdown,.html,.htm,text/plain,text/markdown,text/html" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></FormField>
              <div className="grid gap-3 sm:grid-cols-2">
                <FormField label="数据范围" htmlFor="upload-data-scope"><select id="upload-data-scope" className="form-control" value={dataScope} onChange={(e) => setDataScope(e.target.value as DataScope)}><option value="personal">个人</option><option value="project">项目</option><option value="department">部门</option><option value="enterprise">企业</option></select></FormField>
                <FormField label="安全级别" htmlFor="upload-security"><select id="upload-security" className="form-control" value={securityLevel} onChange={(e) => setSecurityLevel(e.target.value as SecurityLevel)}><option value="internal">内部</option><option value="department_sensitive">部门敏感</option><option value="confidential">机密</option></select></FormField>
              </div>
              {dataScope === "project" ? <FormField label="Project ID" htmlFor="upload-project-id" hint="当前身份必须属于该项目"><input id="upload-project-id" className="form-control font-mono" value={projectId} onChange={(e) => setProjectId(e.target.value)} required /></FormField> : null}
              <AsyncNotice pending={uploadAction.pending} error={uploadValidationError ?? uploadAction.error} />
              {uploadAction.data?.status === "failed" ? <AsyncNotice error="源文件已保存为企业资产，但内容索引失败；请检查文件内容后重试。" /> : null}
              {uploadAction.data ? <div className="notice-box justify-between"><span>{uploadAction.data.filename}</span><StatusBadge status={uploadAction.data.status} /></div> : null}
              <ActionButton type="submit" disabled={uploadAction.pending}><FileUp size={14} />上传并索引</ActionButton>
            </form>
          </SectionCard>
          <SectionCard title="权限检索" description="结果展示真实 citation、相关度与 Trace，不生成不存在的引用" action={<Search size={16} className="text-accent-violet" />}>
            <form className="flex gap-2 max-sm:flex-col" onSubmit={onSearch}>
              <input className="form-control flex-1" aria-label="检索内容" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="输入制度、项目或业务问题" />
              <ActionButton type="submit" disabled={searchAction.pending}><Search size={14} />检索</ActionButton>
            </form>
            <div className="mt-3"><AsyncNotice pending={searchAction.pending} error={searchValidationError ?? searchAction.error} empty={searchAction.status === "success" && searchAction.data?.citations.length === 0} emptyText="没有找到匹配片段" /></div>
            {searchAction.data ? <p className="mt-3 font-mono text-[8px] text-faint">TRACE · {searchAction.data.traceId}</p> : null}
            <div className="mt-3 grid gap-2">
              {searchAction.data?.citations.map((citation) => (
                <article className="rounded-xl border border-line bg-black/10 p-3" key={citation.chunkId}>
                  <div className="mb-2 flex items-center justify-between gap-2"><span className="font-mono text-[8px] text-accent-cyan">{citation.chunkId}</span><strong className="text-[9px] text-accent-green">{Math.round(citation.score * 100)}%</strong></div>
                  <blockquote className="m-0 text-[10px] leading-5 text-ink">{citation.quote}</blockquote>
                  <p className="mt-2 break-all text-[8px] text-faint">资产 {citation.assetId}</p>
                </article>
              ))}
            </div>
          </SectionCard>
        </div>
      </div>
    </PageShell>
  );
}
