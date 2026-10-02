"""Loads one completed run's facts and runs Phase 10.5's pure tracer over them
(`GET /repos/{id}/tracer`, Phase 10.6).

Everything here is deterministic and instant — no LLM. The pure pieces
(`analysis/layers.py::classify_layer`, `analysis/trace.py::trace_from`) know
nothing about the database, so this module is the glue: it classifies every
file's layer (a file that owns an `ApiEndpoint` is a route), builds the forward
import adjacency, resolves each file's external packages to *named* services
(`services/external_services.py`, the same recognition the Architecture screen
uses), and picks the start file from an endpoint or a file id.

A package `resolve_external_target` does not individually recognise comes back
as the generic "External Dependencies" bucket; that is dropped here (express,
axios and friends would be noise on every hop), so a hop lists only services the
curated vocabulary names — same limit as Architecture, recorded in Known Issue
#42.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.dependencies import DependencyEdgeType
from app.analysis.layers import classify_layer
from app.analysis.trace import MAX_DEPTH, MAX_HOPS, ExternalRef, Trace, trace_from
from app.models.analysis_run import AnalysisRun
from app.models.api_endpoint import ApiEndpoint
from app.models.file import File
from app.models.symbol import Symbol
from app.schemas.tracer import TraceExternalRead, TraceHopRead, TracerRead, TraceStart
from app.services import analysis_read_service
from app.services.external_services import EXTERNAL_NODE_ID, resolve_external_target

# Bounds how many symbol names a hop carries (the real total is `symbol_count`).
MAX_SYMBOLS_PER_HOP = 12


class TraceTargetNotFound(Exception):
    """The endpoint/file id does not exist in the run being traced."""


@dataclass
class TraceBundle:
    run: AnalysisRun
    start: TraceStart
    trace: Trace
    loc_by_file: dict[str, int] = field(default_factory=dict)
    # Source-ordered symbol names per hop file (uncapped).
    symbols_by_file: dict[str, list[str]] = field(default_factory=dict)


def build_trace(
    db: Session, run: AnalysisRun, *, endpoint_id: uuid.UUID | None = None, file_id: uuid.UUID | None = None
) -> TraceBundle:
    """Exactly one of `endpoint_id` / `file_id`. Raises `TraceTargetNotFound`
    when the id is not part of `run` (so an id from an older run is a miss)."""
    files = db.execute(select(File).where(File.analysis_run_id == run.id)).scalars().all()
    file_by_id = {f.id: f for f in files}
    endpoints = db.execute(select(ApiEndpoint).where(ApiEndpoint.analysis_run_id == run.id)).scalars().all()
    endpoint_file_ids = {e.file_id for e in endpoints}
    endpoints_per_file: dict[uuid.UUID, int] = defaultdict(int)
    for e in endpoints:
        endpoints_per_file[e.file_id] += 1

    handler_name: str | None = None
    if endpoint_id is not None:
        endpoint = next((e for e in endpoints if e.id == endpoint_id), None)
        if endpoint is None or endpoint.file_id not in file_by_id:
            raise TraceTargetNotFound("endpoint")
        start_file = file_by_id[endpoint.file_id]
        handler_name = _handler_name(db, endpoint)
        start = TraceStart(
            kind="endpoint",
            label=f"{endpoint.method} {endpoint.path}",
            file_path=start_file.path,
            endpoint_id=endpoint.id,
            method=endpoint.method,
            path=endpoint.path,
            endpoints_in_file=endpoints_per_file[start_file.id],
        )
    elif file_id is not None:
        start_file = file_by_id.get(file_id)
        if start_file is None:
            raise TraceTargetNotFound("file")
        start = TraceStart(
            kind="file",
            label=start_file.path,
            file_path=start_file.path,
            endpoints_in_file=endpoints_per_file[start_file.id],
        )
    else:
        raise ValueError("build_trace needs an endpoint_id or a file_id")

    layer_of = {f.path: classify_layer(f.path, f.module, f.id in endpoint_file_ids) for f in files}

    adjacency: dict[str, set[str]] = defaultdict(set)
    externals: dict[str, dict[str, ExternalRef]] = defaultdict(dict)
    for edge in analysis_read_service.load_dependency_edges(db, run.id):
        if edge.edge_type is DependencyEdgeType.INTERNAL and edge.target_file is not None:
            adjacency[edge.source_file].add(edge.target_file)
        elif edge.edge_type is DependencyEdgeType.EXTERNAL and edge.target_package:
            resolved = resolve_external_target(edge.target_package, run.database_provider)
            if resolved is None or resolved[0] == EXTERNAL_NODE_ID:
                continue
            node_id, label, kind = resolved
            externals[edge.source_file][f"{node_id}|{edge.target_package}"] = ExternalRef(
                service_id=node_id, label=label, kind=kind, package=edge.target_package
            )

    trace = trace_from(
        start_file.path,
        adjacency,
        {path: sorted(refs.values(), key=lambda r: (r.service_id, r.package)) for path, refs in externals.items()},
        layer_of,
        handler_name,
    )

    hop_paths = {hop.file_path for hop in trace.hops}
    hop_file_ids = [f.id for f in files if f.path in hop_paths]
    symbols_by_file: dict[str, list[str]] = defaultdict(list)
    if hop_file_ids:
        path_by_id = {f.id: f.path for f in files}
        rows = db.execute(
            select(Symbol.file_id, Symbol.name, Symbol.line_start)
            .where(Symbol.analysis_run_id == run.id, Symbol.file_id.in_(hop_file_ids))
        ).all()
        for file_id_, name, _line in sorted(rows, key=lambda r: (r.line_start, r.name)):
            symbols_by_file[path_by_id[file_id_]].append(name)

    return TraceBundle(
        run=run,
        start=start,
        trace=trace,
        loc_by_file={f.path: f.loc for f in files if f.path in hop_paths},
        symbols_by_file=dict(symbols_by_file),
    )


def to_read(bundle: TraceBundle) -> TracerRead:
    trace = bundle.trace
    hops = []
    for hop in trace.hops:
        symbols = bundle.symbols_by_file.get(hop.file_path, [])
        hops.append(
            TraceHopRead(
                index=hop.index,
                depth=hop.depth,
                parent_index=hop.parent_index,
                layer=hop.layer.value,
                file_path=hop.file_path,
                fn=hop.fn,
                via=list(hop.via),
                deps=list(hop.deps),
                external_services=[
                    TraceExternalRead(
                        service_id=ext.service_id,
                        label=ext.label,
                        kind=ext.kind,
                        packages=list(ext.packages),
                        via=list(ext.via),
                    )
                    for ext in hop.external_services
                ],
                loc=bundle.loc_by_file.get(hop.file_path, 0),
                symbols=symbols[:MAX_SYMBOLS_PER_HOP],
                symbol_count=len(symbols),
            )
        )
    return TracerRead(
        analyzed=True,
        analysis_run_id=bundle.run.id,
        commit_sha=bundle.run.commit_sha,
        branch=bundle.run.branch,
        start=bundle.start,
        hops=hops,
        layers_reached=[layer.value for layer in trace.layers_reached],
        reaches_data=trace.reaches_data,
        truncated=trace.truncated,
        max_depth=MAX_DEPTH,
        max_hops=MAX_HOPS,
    )


def _handler_name(db: Session, endpoint: ApiEndpoint) -> str | None:
    """The real linked `Symbol`'s name when the extractor resolved one, else the
    raw handler reference text (`trace_from` turns an `<inline>` placeholder
    into no name)."""
    if endpoint.handler_symbol_id is not None:
        symbol = db.get(Symbol, endpoint.handler_symbol_id)
        if symbol is not None:
            return symbol.name
    return endpoint.handler_name
