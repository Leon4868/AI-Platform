import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const schemas = join(root, "schemas");
const examples = join(root, "examples");

const readJson = (path) => JSON.parse(readFileSync(path, "utf8"));
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};
const assertNonEmptyString = (value, message) => assert(typeof value === "string" && value.length > 0, message);
const assertOnlyKeys = (value, allowedKeys, message) => {
  const unexpected = Object.keys(value).filter((key) => !allowedKeys.includes(key));
  assert(unexpected.length === 0, `${message}: unexpected keys ${unexpected.join(", ")}`);
};

const schemaFiles = readdirSync(schemas).filter((name) => name.endsWith(".json"));
const exampleFiles = readdirSync(examples).filter((name) => name.endsWith(".json"));
for (const name of schemaFiles) {
  const schema = readJson(join(schemas, name));
  assert(schema.$schema?.includes("2020-12"), `${name}: expected JSON Schema 2020-12`);
  assert(schema.$id, `${name}: missing $id`);
}

const definition = readJson(join(examples, "workflow-definition.example.json"));
const nodeIds = new Set(definition.nodes.map((node) => node.id));
assert(nodeIds.size === definition.nodes.length, "workflow definition: duplicate node id");
assert(nodeIds.has(definition.entryNodeId), "workflow definition: entry node does not exist");
for (const edge of definition.edges) {
  assert(nodeIds.has(edge.sourceNodeId), `workflow definition: unknown source ${edge.sourceNodeId}`);
  assert(nodeIds.has(edge.targetNodeId), `workflow definition: unknown target ${edge.targetNodeId}`);
  assert(edge.sourceNodeId !== edge.targetNodeId, `workflow definition: self-loop ${edge.id}`);
}

const adjacency = new Map([...nodeIds].map((id) => [id, []]));
for (const edge of definition.edges) adjacency.get(edge.sourceNodeId).push(edge.targetNodeId);
const visiting = new Set();
const visited = new Set();
const visit = (id) => {
  assert(!visiting.has(id), `workflow definition: cycle detected at ${id}`);
  if (visited.has(id)) return;
  visiting.add(id);
  for (const next of adjacency.get(id)) visit(next);
  visiting.delete(id);
  visited.add(id);
};
visit(definition.entryNodeId);
assert(visited.size === nodeIds.size, "workflow definition: unreachable node exists");

const run = readJson(join(examples, "workflow-run.example.json"));
const workflowSchema = readJson(join(schemas, "workflow.schema.json"));
const runStatuses = new Set(workflowSchema.$defs.run.properties.status.enum);
const nodeRunStatuses = new Set(workflowSchema.$defs.nodeRun.properties.status.enum);
const runEventTypes = new Set(workflowSchema.$defs.runEvent.properties.type.enum);
assert(run.workflowDefinitionId === definition.id, "workflow run: definition id mismatch");
assert(run.workflowDefinitionVersion === definition.definitionVersion, "workflow run: definition version mismatch");
assert(runStatuses.has(run.status), `workflow run: unknown status ${run.status}`);
assert(run.nodeRuns.every((item) => nodeIds.has(item.nodeId)), "workflow run: unknown node id");
assert(run.nodeRuns.every((item) => nodeRunStatuses.has(item.status)), "workflow run: unknown node status");

const startRun = readJson(join(examples, "workflow-run-start.example.json"));
assertOnlyKeys(startRun, ["workflowDefinitionVersion", "input"], "workflow run start");
assert(startRun.workflowDefinitionVersion >= 1, "workflow run start: invalid definition version");
assert(Object.hasOwn(startRun, "input"), "workflow run start: input is required");

const cancelRun = readJson(join(examples, "workflow-run-cancel.example.json"));
assertOnlyKeys(cancelRun, ["reason"], "workflow run cancel");
assertNonEmptyString(cancelRun.reason, "workflow run cancel: non-empty reason is required when present");

const runEvents = readJson(join(examples, "workflow-run-events.example.json"));
assert(Array.isArray(runEvents) && runEvents.length > 0, "workflow events: expected non-empty array");
const eventsByRun = new Map();
for (const event of runEvents) {
  assert(Number.isInteger(event.sequence) && event.sequence >= 1, "workflow event: sequence must be a positive integer");
  assertNonEmptyString(event.runId, "workflow event: runId is required");
  assertNonEmptyString(event.type, "workflow event: type is required");
  assert(runEventTypes.has(event.type), `workflow event: unknown type ${event.type}`);
  assertNonEmptyString(event.occurredAt, "workflow event: occurredAt is required");
  assert(Object.hasOwn(event, "payload"), "workflow event: payload is required");
  assert(!event.type.startsWith("node.") || event.nodeId, `workflow event: ${event.type} requires nodeId`);
  const previous = eventsByRun.get(event.runId)?.at(-1);
  assert(!previous || event.sequence > previous.sequence, `workflow event: non-monotonic sequence for ${event.runId}`);
  const grouped = eventsByRun.get(event.runId) ?? [];
  grouped.push(event);
  eventsByRun.set(event.runId, grouped);
}
const representedEventTypes = new Set(runEvents.map((event) => event.type));
for (const type of [
  "run.started", "run.completed", "run.failed", "run.cancelled",
  "node.started", "node.completed", "node.failed", "node.cancelled",
]) {
  assert(representedEventTypes.has(type), `workflow events: missing lifecycle example ${type}`);
}

const asset = readJson(join(examples, "asset.example.json"));
assert(asset.workflowRunId === run.id, "asset: workflow run lineage mismatch");
assert(asset.traceId === run.traceId, "asset: trace lineage mismatch");

const trace = readJson(join(examples, "trace.example.json"));
assert(trace.traceId === run.traceId, "trace: run trace id mismatch");
assert(trace.runId === run.id, "trace: run id mismatch");

const knowledgeDocument = readJson(join(examples, "knowledge-document.example.json"));
assert(knowledgeDocument.knowledgeBaseId === "kb_product", "knowledge document: knowledge base mismatch");

const search = readJson(join(examples, "knowledge-search.example.json"));
assert(search.topK >= 1 && search.topK <= 50, "knowledge search: topK out of range");

const documentRequest = readJson(join(examples, "document-generation.example.json"));
assert(documentRequest.workflowDefinitionId === definition.id, "document request: definition id mismatch");
assert(documentRequest.logicalModelCode, "document request: logical model is required");

const openapi = readFileSync(join(root, "openapi.yaml"), "utf8");
for (const path of [
  "/v1/workflows/{workflow_id}/runs:",
  "/v1/workflow-runs/{run_id}:",
  "/v1/workflow-runs/{run_id}/cancel:",
  "/v1/workflow-runs/{run_id}/events:",
]) {
  assert(openapi.includes(path), `openapi: missing path ${path}`);
}
for (const sseRule of [
  "Last-Event-ID",
  "id: sequence",
  "event: type",
  "data: WorkflowRunEvent",
  "x-sse-data-schema:",
  "./schemas/workflow.schema.json#/$defs/runEvent",
]) {
  assert(openapi.includes(sseRule), `openapi: missing SSE rule ${sseRule}`);
}

const typescript = readFileSync(join(root, "src", "types.ts"), "utf8");
for (const mirroredContract of [
  "StartWorkflowRunRequest",
  "CancelWorkflowRunRequest",
  '"waiting_human"',
  '"node.cancelled"',
  '"run.cancelled"',
]) {
  assert(typescript.includes(mirroredContract), `typescript mirror: missing ${mirroredContract}`);
}

console.log(
  `Contract validation OK: ${schemaFiles.length} schemas, ${exampleFiles.length} examples, ` +
  `${eventsByRun.size} event streams, workflow graph and HTTP/SSE invariants`,
);
