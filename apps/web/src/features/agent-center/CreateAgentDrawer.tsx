import { useEffect, useRef, useState, type FormEvent } from "react";
import { Bot, LoaderCircle, Plus, X } from "lucide-react";

import { Glass } from "../../components/ui/Glass";
import { ActionButton, AsyncNotice, FormField } from "../../components/ui/Workbench";
import type { CreateAgentInput, ManageableDepartment } from "./types";

const emptyForm: CreateAgentInput = {
  name: "",
  description: "",
  ownerDepartmentId: "",
};

type CreateAgentDrawerProps = {
  open: boolean;
  pending: boolean;
  error?: string;
  departments: ManageableDepartment[];
  departmentsPending: boolean;
  departmentsError?: string;
  onClose: () => void;
  onRetryDepartments: () => void;
  onSubmit: (input: CreateAgentInput) => void | Promise<void>;
};

export function CreateAgentDrawer({
  open,
  pending,
  error,
  departments,
  departmentsPending,
  departmentsError,
  onClose,
  onRetryDepartments,
  onSubmit,
}: CreateAgentDrawerProps) {
  const [form, setForm] = useState<CreateAgentInput>(emptyForm);
  const nameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setForm(emptyForm);
      return;
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pending) onClose();
    };

    document.addEventListener("keydown", onKeyDown);
    nameInputRef.current?.focus();
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, open, pending]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;

    await onSubmit({
      name: form.name.trim(),
      description: form.description.trim(),
      ownerDepartmentId: form.ownerDepartmentId.trim(),
    });
  };

  const close = () => {
    if (pending) return;
    setForm(emptyForm);
    onClose();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/52 backdrop-blur-[2px]" role="presentation" onMouseDown={close}>
      <Glass
        as="aside"
        strength="strong"
        className="flex h-full w-[min(100%,430px)] flex-col rounded-none border-y-0 border-r-0 shadow-[0_0_90px_rgb(0_0_0_/_55%)]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-agent-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-5">
          <div className="min-w-0">
            <div className="mb-2 flex items-center gap-2 text-accent-cyan">
              <Bot size={15} />
              <p className="text-[8px] font-black tracking-[.16em]">AGENT DRAFT</p>
            </div>
            <h2 id="create-agent-title" className="m-0 text-lg font-semibold">新建 Agent</h2>
            <p className="mt-1.5 text-[9px] leading-4 text-muted">先创建可审计的 Agent 草稿，创建成功后进入该 Agent 的编排空间。</p>
          </div>
          <button
            className="subtle-action shrink-0"
            type="button"
            aria-label="关闭新建 Agent"
            disabled={pending}
            onClick={close}
          >
            <X size={16} />
          </button>
        </header>

        <form className="flex min-h-0 flex-1 flex-col" onSubmit={submit}>
          <div className="grid flex-1 content-start gap-4 overflow-y-auto p-5 [scrollbar-color:rgb(121_175_214_/_26%)_transparent] [scrollbar-width:thin]">
            <FormField label="Agent 名称" htmlFor="agent-name" hint="用于 Agent 中心、编排标题和运行审计。">
              <input
                ref={nameInputRef}
                id="agent-name"
                className="form-control"
                value={form.name}
                maxLength={120}
                required
                disabled={pending}
                placeholder="例如：企业文档助手"
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
              />
            </FormField>

            <FormField label="用途说明" htmlFor="agent-description" hint="说明 Agent 的职责边界，不要在此填写密钥或访问凭证。">
              <textarea
                id="agent-description"
                className="form-control min-h-28 resize-y"
                value={form.description}
                maxLength={2000}
                disabled={pending}
                placeholder="描述该 Agent 解决的问题与服务对象"
                onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
              />
            </FormField>

            <FormField label="责任部门" htmlFor="agent-owner-department" hint="仅可选择当前身份有权管理的部门。">
              <select
                id="agent-owner-department"
                className="form-control"
                value={form.ownerDepartmentId}
                required
                disabled={pending || departmentsPending || Boolean(departmentsError) || departments.length === 0}
                onChange={(event) => setForm((current) => ({ ...current, ownerDepartmentId: event.target.value }))}
              >
                <option value="">请选择责任部门</option>
                {departments.map((department) => (
                  <option key={department.id} value={department.id}>{department.name}</option>
                ))}
              </select>
            </FormField>

            {departmentsPending ? <AsyncNotice pending /> : null}
            {!departmentsPending && departmentsError ? (
              <div className="grid gap-2">
                <AsyncNotice error={departmentsError} />
                <ActionButton variant="secondary" type="button" onClick={onRetryDepartments}>重新加载部门</ActionButton>
              </div>
            ) : null}
            {!departmentsPending && !departmentsError && departments.length === 0 ? (
              <AsyncNotice empty emptyText="当前身份没有可管理的责任部门，无法创建 Agent。" />
            ) : null}

            <AsyncNotice pending={pending} error={error} />
          </div>

          <footer className="flex justify-end gap-2 border-t border-line p-4">
            <ActionButton variant="secondary" type="button" disabled={pending} onClick={close}>取消</ActionButton>
            <ActionButton type="submit" disabled={pending || departmentsPending || Boolean(departmentsError) || departments.length === 0}>
              {pending ? <LoaderCircle className="animate-[spin_.9s_linear_infinite]" size={14} /> : <Plus size={14} />}
              {pending ? "正在创建…" : "创建并进入编排"}
            </ActionButton>
          </footer>
        </form>
      </Glass>
    </div>
  );
}
