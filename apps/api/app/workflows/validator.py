from collections import defaultdict, deque

from app.workflows.schemas import (
    NodeType,
    WorkflowGraph,
    WorkflowValidationIssue,
    WorkflowValidationResult,
)


class WorkflowGraphValidator:
    def validate(self, graph: WorkflowGraph) -> WorkflowValidationResult:
        issues: list[WorkflowValidationIssue] = []
        nodes = {node.id: node for node in graph.nodes}
        outgoing: dict[str, list[str]] = defaultdict(list)
        incoming: dict[str, list[str]] = defaultdict(list)

        starts = [node for node in graph.nodes if node.type == NodeType.START]
        ends = [node for node in graph.nodes if node.type == NodeType.END]
        if len(starts) != 1:
            issues.append(self._issue("start_count", "A workflow must contain exactly one start node"))
        if not ends:
            issues.append(self._issue("end_count", "A workflow must contain at least one end node"))

        seen_connections: set[tuple[str, str, str | None]] = set()
        for edge in graph.edges:
            if edge.source not in nodes:
                issues.append(self._issue("unknown_source", "Edge source does not exist", edge_id=edge.id))
                continue
            if edge.target not in nodes:
                issues.append(self._issue("unknown_target", "Edge target does not exist", edge_id=edge.id))
                continue
            if edge.source == edge.target:
                issues.append(self._issue("self_loop", "Self-loop edges are not supported", edge_id=edge.id))
                continue
            connection = (edge.source, edge.target, edge.label)
            if connection in seen_connections:
                issues.append(self._issue("duplicate_edge", "Duplicate edge", edge_id=edge.id))
            seen_connections.add(connection)
            outgoing[edge.source].append(edge.target)
            incoming[edge.target].append(edge.source)

        for node in graph.nodes:
            if node.type == NodeType.START and incoming[node.id]:
                issues.append(self._issue("start_has_input", "Start node cannot have incoming edges", node_id=node.id))
            if node.type == NodeType.END and outgoing[node.id]:
                issues.append(self._issue("end_has_output", "End node cannot have outgoing edges", node_id=node.id))
            if node.type != NodeType.START and not incoming[node.id]:
                issues.append(self._issue("missing_input", "Node requires an incoming edge", node_id=node.id))
            if node.type != NodeType.END and not outgoing[node.id]:
                issues.append(self._issue("missing_output", "Node requires an outgoing edge", node_id=node.id))
            if node.type in {NodeType.CONDITION, NodeType.PARALLEL} and len(outgoing[node.id]) < 2:
                issues.append(
                    self._issue("branch_count", "Branch node requires at least two outgoing edges", node_id=node.id)
                )

        if len(starts) == 1:
            reachable = self._reachable(starts[0].id, outgoing)
            for node_id in nodes.keys() - reachable:
                issues.append(self._issue("unreachable_node", "Node is not reachable from start", node_id=node_id))

        if self._has_cycle(nodes, outgoing):
            issues.append(self._issue("cycle", "Cycles are not supported in workflow schema 1.x"))

        return WorkflowValidationResult(valid=not issues, errors=issues)

    @staticmethod
    def _reachable(start_id: str, outgoing: dict[str, list[str]]) -> set[str]:
        visited: set[str] = set()
        queue = deque([start_id])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            queue.extend(outgoing[current])
        return visited

    @staticmethod
    def _has_cycle(nodes: dict[str, object], outgoing: dict[str, list[str]]) -> bool:
        indegree = {node_id: 0 for node_id in nodes}
        for targets in outgoing.values():
            for target in targets:
                if target in indegree:
                    indegree[target] += 1
        queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
        visited = 0
        while queue:
            current = queue.popleft()
            visited += 1
            for target in outgoing[current]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        return visited != len(nodes)

    @staticmethod
    def _issue(
        code: str,
        message: str,
        *,
        node_id: str | None = None,
        edge_id: str | None = None,
    ) -> WorkflowValidationIssue:
        return WorkflowValidationIssue(code=code, message=message, node_id=node_id, edge_id=edge_id)
