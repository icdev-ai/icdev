# CUI // SP-CTI
"""Scale Chunked Pipeline — process Arrow data in fixed-size chunks (D-SC-1).

Wraps ArrowPipeline to avoid full-table materialization. Processes data
in configurable chunks, concatenating results or yielding per-chunk.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import time
from typing import Any, Dict, Iterator, List, Optional

logger = get_logger("databridge.scale.chunked_pipeline")

try:
    import pyarrow as pa

    HAS_ARROW = True
except ImportError:
    HAS_ARROW = False


class ChunkedPipeline:
    """Process Arrow data in chunks through ArrowPipeline (D-SC-1).

    Instead of materializing the full table, processes batch_size rows
    at a time through the pipeline.
    """

    def __init__(
        self,
        pipeline: Any = None,
        chunk_size: int = 10000,
    ):
        """
        Args:
            pipeline: ArrowPipeline instance (or None for pass-through)
            chunk_size: Number of rows per chunk
        """
        self._pipeline = pipeline
        self._chunk_size = chunk_size

    def execute(self, data: Any) -> Dict[str, Any]:
        """Process data in chunks, concatenate results.

        Returns same shape as ArrowPipeline.execute():
            {status, data, row_count, column_count, duration_ms, errors}
        """
        if not HAS_ARROW:
            return {
                "status": "error",
                "errors": ["pyarrow not installed"],
                "data": data,
                "row_count": 0,
                "duration_ms": 0,
            }

        start = time.time()
        table = _normalize_to_table(data)
        if table is None:
            return {
                "status": "error",
                "errors": ["Could not normalize input to Arrow Table"],
                "data": data,
                "row_count": 0,
                "duration_ms": 0,
            }

        if self._pipeline is None:
            # Pass-through mode — no transforms, just chunk for memory control
            duration_ms = int((time.time() - start) * 1000)
            return {
                "status": "ok",
                "data": table,
                "row_count": table.num_rows,
                "column_count": table.num_columns,
                "chunks_processed": 1,
                "duration_ms": duration_ms,
                "errors": [],
            }

        results: List[Any] = []
        all_errors: List[str] = []
        chunks_processed = 0

        for i in range(0, table.num_rows, self._chunk_size):
            chunk = table.slice(i, self._chunk_size)
            result = self._pipeline.execute(chunk)

            if result.get("data") is not None:
                results.append(result["data"])
            if result.get("errors"):
                all_errors.extend(result["errors"])
            chunks_processed += 1

        # Concatenate results
        if results:
            final_table = pa.concat_tables([_normalize_to_table(r) for r in results if r is not None])
        else:
            final_table = table.slice(0, 0)  # empty with same schema

        duration_ms = int((time.time() - start) * 1000)
        status = "ok" if not all_errors else "partial"

        return {
            "status": status,
            "data": final_table,
            "row_count": final_table.num_rows,
            "column_count": final_table.num_columns,
            "chunks_processed": chunks_processed,
            "chunk_size": self._chunk_size,
            "duration_ms": duration_ms,
            "errors": all_errors,
        }

    def execute_streaming(self, data: Any) -> Iterator[Dict[str, Any]]:
        """Yield per-chunk results instead of concatenating.

        Useful for write pipelines that flush incrementally.
        """
        if not HAS_ARROW:
            yield {
                "status": "error",
                "errors": ["pyarrow not installed"],
                "data": data,
                "chunk_index": 0,
            }
            return

        table = _normalize_to_table(data)
        if table is None:
            yield {
                "status": "error",
                "errors": ["Could not normalize input to Arrow Table"],
                "data": data,
                "chunk_index": 0,
            }
            return

        for chunk_idx, i in enumerate(range(0, table.num_rows, self._chunk_size)):
            chunk = table.slice(i, self._chunk_size)
            start = time.time()

            if self._pipeline:
                result = self._pipeline.execute(chunk)
            else:
                result = {
                    "status": "ok",
                    "data": chunk,
                    "row_count": chunk.num_rows,
                    "errors": [],
                }

            result["chunk_index"] = chunk_idx
            result["chunk_offset"] = i
            result["duration_ms"] = int((time.time() - start) * 1000)
            yield result


def _normalize_to_table(data: Any) -> Optional[Any]:
    """Convert RecordBatch or Table to Table."""
    if not HAS_ARROW:
        return None
    if isinstance(data, pa.Table):
        return data
    if isinstance(data, pa.RecordBatch):
        return pa.Table.from_batches([data])
    return None
