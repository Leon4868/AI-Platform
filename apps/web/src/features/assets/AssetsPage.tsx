import { useCallback, useState, type FormEvent } from "react";
import { Boxes, ExternalLink, Search } from "lucide-react";

import { ActionButton, AsyncNotice, FormField, KeyValue, PageShell, SectionCard, StatusBadge } from "../../components/ui/Workbench";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { isBrowserDownloadUri } from "../enterprise-api/download";
import type { EnterpriseApi } from "../enterprise-api/types";

export function AssetsPage({ api }: { api: EnterpriseApi }) {
  const [assetId, setAssetId] = useState("");
  const request = useCallback((id: string, signal: AbortSignal) => api.getAsset(id, signal), [api]);
  const lookup = useAsyncAction(request);
  const onSubmit = (event: FormEvent) => { event.preventDefault(); if (assetId.trim()) void lookup.run(assetId.trim()); };
  const downloadable = isBrowserDownloadUri(lookup.data?.storageUri);

  return (
    <PageShell eyebrow="ENTERPRISE ASSETS" title="企业资产" description="通过资产 ID 读取当前身份可见的元数据、血缘与短期下载地址。" transportKind={api.kind}>
      <div className="mx-auto grid w-full max-w-4xl gap-4">
        <SectionCard title="查找企业资产" description="跨租户或无权访问时统一按不可访问处理" action={<Boxes size={17} className="text-accent-green" />}>
          <form className="flex gap-2 max-sm:flex-col" onSubmit={onSubmit}>
            <FormField className="flex-1" label="Asset ID" htmlFor="asset-id"><input id="asset-id" className="form-control font-mono" value={assetId} onChange={(e) => setAssetId(e.target.value)} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" /></FormField>
            <ActionButton className="self-end" type="submit" disabled={lookup.pending}><Search size={14} />查询</ActionButton>
          </form>
          <div className="mt-3"><AsyncNotice pending={lookup.pending} error={lookup.error} empty={lookup.status === "idle"} emptyText="输入 Asset ID 查看资产" /></div>
        </SectionCard>
        {lookup.data ? <SectionCard title={lookup.data.name} description={lookup.data.description} action={<StatusBadge status={lookup.data.status} />}>
          <div className="grid gap-x-6 md:grid-cols-2"><div><KeyValue label="Asset ID" value={lookup.data.id} mono /><KeyValue label="类型" value={lookup.data.type} /><KeyValue label="版本" value={`v${lookup.data.version}`} /><KeyValue label="MIME" value={lookup.data.mimeType} /></div><div><KeyValue label="部门" value={lookup.data.ownerDepartmentId} /><KeyValue label="范围" value={lookup.data.dataScope} /><KeyValue label="安全级别" value={lookup.data.securityLevel} /><KeyValue label="Trace" value={lookup.data.traceId} mono /></div></div>
          <div className="mt-4 flex gap-2">{downloadable ? <a className="action-button action-button-primary" href={lookup.data.storageUri} target="_blank" rel="noreferrer"><ExternalLink size={13} />下载</a> : <AsyncNotice empty emptyText={lookup.data.storageUri ? "当前存储后端不支持浏览器下载" : "资产没有可下载对象"} />}</div>
        </SectionCard> : null}
      </div>
    </PageShell>
  );
}
