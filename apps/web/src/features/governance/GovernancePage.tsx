import { Boxes, CircleDollarSign, GitBranch, HardDrive, Rocket, Users, Workflow } from "lucide-react";

import { ActionButton, AsyncNotice, FormField, KeyValue, PageShell, SectionCard } from "../../components/ui/Workbench";
import { ApiPendingPanel, PreviewMetric, PreviewStatus } from "./GovernancePreview";

const resourceMetrics = [
  { label: "成员席位", description: "租户成员总量、活跃席位与身份同步状态", icon: Users },
  { label: "知识与资产", description: "知识库、企业资产数量与存储占用", icon: Boxes },
  { label: "模型调用", description: "月度请求、Token 与供应商用量汇总", icon: CircleDollarSign },
  { label: "工作流并发", description: "运行中任务、并发额度与队列水位", icon: Workflow },
] as const;

export function GovernancePage() {
  return (
    <PageShell
      eyebrow="PLATFORM GOVERNANCE"
      title="平台治理"
      description="面向平台管理员的租户资源、模型成本、版本与发布治理预览。当前仅展示信息架构，所有实时数据与写操作等待治理 API 接入。"
      actions={<PreviewStatus />}
    >
      <div className="grid gap-4 2xl:grid-cols-[minmax(0,1.25fr)_minmax(360px,.75fr)]">
        <div className="grid content-start gap-4">
          <SectionCard title="租户资源概览" description="数据接入后按当前企业租户和管理员权限聚合" action={<HardDrive size={17} className="text-accent-cyan" />}>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-2">
              {resourceMetrics.map((metric) => <PreviewMetric key={metric.label} {...metric} />)}
            </div>
          </SectionCard>

          <SectionCard title="模型预算与用量" description="计划支持逻辑模型、供应商和部门维度的预算与告警" action={<CircleDollarSign size={17} className="text-accent-green" />}>
            <div className="grid gap-3 lg:grid-cols-[minmax(0,1.35fr)_minmax(240px,.65fr)]">
              <ApiPendingPanel title="本预算周期" description="预算上限、已用金额、Token 与调用量尚未接入">
                <div className="grid gap-1.5">
                  <KeyValue label="预算上限" />
                  <KeyValue label="已用金额" />
                  <KeyValue label="输入 / 输出 Token" />
                  <KeyValue label="下次结算时间" />
                </div>
              </ApiPendingPanel>
              <div className="grid content-start gap-2">
                <AsyncNotice empty emptyText="暂无真实模型用量；不会使用示例金额替代。" />
                <ActionButton disabled><CircleDollarSign size={13} />配置预算（待接入）</ActionButton>
              </div>
            </div>
          </SectionCard>

          <SectionCard title="Prompt / Workflow 版本" description="统一查看生产版本、候选版本、变更人与审批状态" action={<GitBranch size={17} className="text-accent-violet" />}>
            <div className="grid gap-3 md:grid-cols-2">
              <ApiPendingPanel title="Prompt 资产版本" description="等待版本清单与审批 API">
                <KeyValue label="生产版本" />
                <KeyValue label="待发布版本" />
                <KeyValue label="最后变更" />
              </ApiPendingPanel>
              <ApiPendingPanel title="Workflow 定义版本" description="等待发布快照与运行兼容性 API">
                <KeyValue label="生产版本" />
                <KeyValue label="待发布版本" />
                <KeyValue label="最后变更" />
              </ApiPendingPanel>
            </div>
          </SectionCard>
        </div>

        <SectionCard className="self-start 2xl:sticky 2xl:top-24" title="灰度发布入口" description="预览发布计划字段；当前不会创建、提交或执行灰度" action={<Rocket size={17} className="text-accent-amber" />}>
          <div className="mb-3"><AsyncNotice empty emptyText="灰度与回滚 API 尚未接入，以下控件仅展示规划。" /></div>
          <form className="grid gap-3" aria-label="灰度发布预览">
            <FormField label="资产类型" htmlFor="governance-release-kind"><select id="governance-release-kind" className="form-control" disabled><option>Prompt / Workflow</option></select></FormField>
            <FormField label="目标资产" htmlFor="governance-release-asset"><input id="governance-release-asset" className="form-control" placeholder="待 API 接入后选择" disabled /></FormField>
            <FormField label="候选版本" htmlFor="governance-release-version"><input id="governance-release-version" className="form-control" placeholder="待 API 接入后选择" disabled /></FormField>
            <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-1">
              <FormField label="灰度比例" htmlFor="governance-release-ratio"><select id="governance-release-ratio" className="form-control" disabled><option>待配置</option></select></FormField>
              <FormField label="观察窗口" htmlFor="governance-release-window"><select id="governance-release-window" className="form-control" disabled><option>待配置</option></select></FormField>
            </div>
            <ApiPendingPanel title="发布保护" description="接入后将要求审批、指标阈值、自动回滚与审计 Trace。" />
            <ActionButton disabled><Rocket size={13} />创建灰度计划（待接入）</ActionButton>
          </form>
        </SectionCard>
      </div>
    </PageShell>
  );
}
