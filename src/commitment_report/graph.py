"""版本化证据图：节点不可变，边记录溯源关系。

- 节点键为 ``(kind, entity_id, version)``，同一键写入两次即报错；
- 修订只能以 ``最新版本号 + 1`` 追加，历史版本永远可读；
- 边表达 ``supported_by``（指标 → 来源材料版本）与
  ``measured_by``（指标 → 测算方法版本），供汇总结果回溯到具体材料。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ConflictError, NotFoundError
from .model import IndicatorVersion, Methodology, SourceMaterial, Target

NodeKey = tuple[str, str, int]  # (kind, entity_id, version)

SUPPORTED_BY = "supported_by"
MEASURED_BY = "measured_by"


@dataclass(frozen=True)
class Edge:
    origin: NodeKey
    relation: str
    target: NodeKey


class EvidenceGraph:
    def __init__(self) -> None:
        self._nodes: dict[NodeKey, object] = {}
        self._latest: dict[tuple[str, str], int] = {}
        self._edges: list[Edge] = []

    # -- 写入 ---------------------------------------------------------------

    def _put(self, kind: str, entity_id: str, version: int, node: object) -> None:
        key = (kind, entity_id, version)
        if key in self._nodes:
            raise ConflictError(f"节点已存在且不可变: {key}")
        expected = self._latest.get((kind, entity_id), 0) + 1
        if version != expected:
            raise ConflictError(
                f"{kind} {entity_id} 下一版本应为 {expected}，收到 {version}"
            )
        self._nodes[key] = node
        self._latest[(kind, entity_id)] = version

    def add_source(self, node: SourceMaterial) -> None:
        self._put("source", node.source_id, node.version, node)

    def add_method(self, node: Methodology) -> None:
        self._put("method", node.method_id, node.version, node)

    def add_target(self, node: Target) -> None:
        self._put("target", node.target_id, node.version, node)

    def add_indicator(self, node: IndicatorVersion) -> None:
        key = ("indicator", node.indicator_id, node.version)
        for source_id, source_version in node.evidence:
            if ("source", source_id, source_version) not in self._nodes:
                raise NotFoundError(f"来源材料不存在: {source_id}@{source_version}")
        if node.method_id is not None:
            method_key = ("method", node.method_id, self.latest_version("method", node.method_id))
            if method_key not in self._nodes:
                raise NotFoundError(f"测算方法不存在: {node.method_id}")
        self._put("indicator", node.indicator_id, node.version, node)
        for source_id, source_version in node.evidence:
            self._edges.append(Edge(key, SUPPORTED_BY, ("source", source_id, source_version)))
        if node.method_id is not None:
            self._edges.append(Edge(key, MEASURED_BY, method_key))

    # -- 读取 ---------------------------------------------------------------

    def latest_version(self, kind: str, entity_id: str) -> int:
        try:
            return self._latest[(kind, entity_id)]
        except KeyError:
            raise NotFoundError(f"实体不存在: {kind} {entity_id}") from None

    def get(self, kind: str, entity_id: str, version: int | None = None):
        if version is None:
            version = self.latest_version(kind, entity_id)
        key = (kind, entity_id, version)
        if key not in self._nodes:
            raise NotFoundError(f"节点不存在: {key}")
        return self._nodes[key]

    def iter_latest(self, kind: str) -> list:
        return [
            self._nodes[(k, entity_id, version)]
            for (k, entity_id), version in sorted(self._latest.items())
            if k == kind
        ]

    def entity_ids(self, kind: str) -> list[str]:
        return sorted(entity_id for (k, entity_id) in self._latest if k == kind)

    # -- 溯源 ---------------------------------------------------------------

    def edges_from(self, key: NodeKey) -> list[Edge]:
        return [e for e in self._edges if e.origin == key]

    def provenance_of(self, indicator_id: str, version: int):
        """返回指标版本直接引用的来源材料与测算方法节点。"""
        key = ("indicator", indicator_id, version)
        sources: list[SourceMaterial] = []
        methods: list[Methodology] = []
        for edge in self.edges_from(key):
            node = self._nodes[edge.target]
            if edge.relation == SUPPORTED_BY:
                sources.append(node)
            elif edge.relation == MEASURED_BY:
                methods.append(node)
        return sources, methods
