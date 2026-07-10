"""
ArcadeDB Writer Module

Writes complete graph data to ArcadeDB:
- Vertices: Directory, File, Function, Method, Class, Interface, Enum, Chunk, Annotation, Tag
- Edges: CONTAINS, IMPORTS, INHERITS, IMPLEMENTS, EMBEDS, ANNOTATES, TAGGED_WITH
"""

import os
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import httpx

logger = logging.getLogger(__name__)


@dataclass
class WriteResult:
    """Result from ArcadeDB write operation."""
    directories: int = 0
    files: int = 0
    functions: int = 0
    methods: int = 0
    classes: int = 0
    inherits: int = 0
    interfaces: int = 0
    enums: int = 0
    # Status
    errors: int = 0
    success: bool = True
    error: Optional[str] = None


class ArcadeDBWriter:
    """Direct ArcadeDB graph writer with UPSERT support."""

    def __init__(self, base_url: str, internal_secret: str, timeout: int = 120, clean: bool = False):
        self.base_url = base_url.rstrip("/")
        self.internal_secret = internal_secret
        self.timeout = timeout
        self.clean = clean

    def write(
        self,
        user_id: str,
        project: str,
        files: List[Dict] = None,
        on_progress: callable = None,
    ) -> WriteResult:
        """
        Write graph data to ArcadeDB using the internal batch endpoint.

        Args:
            user_id: User identifier
            project: Project/database name
            files: List of parsed file results with full AST data
        """
        headers = {
            "X-Internal-Secret": self.internal_secret,
            "X-User-Id": user_id,
            "Content-Type": "application/json"
        }

        result = WriteResult()

        # Prepare file data with all extracted information
        file_data = []
        for f in (files or []):
            file_path = f.get("file_path", "")
            file_data.append({
                "path": file_path,
                "name": os.path.basename(file_path),
                "language": f.get("language", "unknown"),
                # Enriched data
                "summary": f.get("summary"),
                "domain": f.get("domain"),
                "layer": f.get("layer"),
                # Vertex data
                "functions": f.get("functions", []),
                "methods": f.get("methods", []),
                "classes": f.get("classes", []),
                "imports": f.get("imports", []),
                # Semantic skeleton from orchestrator
                "skeleton": f.get("skeleton"),
                # Extended structural elements (exported-only for interfaces/enums)
                "interfaces": [i for i in f.get("interfaces", []) if i.get("is_exported")],
                "enums": [e for e in f.get("enums", []) if e.get("is_exported")],
            })

        def write_graph():
            graph_result = WriteResult()
            BATCH_SIZE = 50
            total_files = len(file_data)

            for batch_start in range(0, max(total_files, 1), BATCH_SIZE):
                batch_end = min(batch_start + BATCH_SIZE, total_files)
                batch_files = file_data[batch_start:batch_end] if file_data else []

                payload = {
                    "project": project,
                    "clean": self.clean and batch_start == 0,
                    "files": batch_files,
                }

                try:
                    response = httpx.post(
                        f"{self.base_url}/internal/graph/upsert-batch",
                        headers=headers,
                        json=payload,
                        timeout=self.timeout
                    )

                    if response.status_code in (200, 201):
                        data = response.json()
                        stats = data.get("stats", {})
                        graph_result.directories += stats.get("directories", 0)
                        graph_result.files += stats.get("files", 0)
                        graph_result.functions += stats.get("functions", 0)
                        graph_result.methods += stats.get("methods", 0)
                        graph_result.classes += stats.get("classes", 0)
                        graph_result.inherits += stats.get("inherits", 0)
                        graph_result.interfaces += stats.get("interfaces", 0)
                        graph_result.enums += stats.get("enums", 0)
                    else:
                        logger.error(f"Batch failed: {response.status_code} - {response.text}")
                        graph_result.errors += len(batch_files)

                except Exception as e:
                    logger.error(f"Batch request error: {e}")
                    graph_result.errors += len(batch_files)
                    graph_result.success = False
                    graph_result.error = str(e)

                if total_files > 0:
                    logger.info(f"Write progress: {batch_end}/{total_files} files")
                    if on_progress:
                        on_progress(batch_end, total_files, graph_result)

            return graph_result

        result = write_graph()
        logger.info(f"ArcadeDB write complete: {result}")
        return result

    def delete_files(self, user_id: str, project: str, files: List[str]) -> bool:
        """
        Delete vertices and edges for removed files via the delete-files endpoint.
        """
        if not files:
            return True

        headers = {
            "X-Internal-Secret": self.internal_secret,
            "X-User-Id": user_id,
            "Content-Type": "application/json"
        }

        try:
            response = httpx.post(
                f"{self.base_url}/internal/graph/delete-files",
                headers=headers,
                json={"project": project, "files": files},
                timeout=self.timeout
            )

            if response.status_code in (200, 201):
                data = response.json()
                logger.info(f"Deleted {data.get('deleted', 0)} files from graph")
                return True
            else:
                logger.error(f"Delete files failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Delete files request error: {e}")
            return False
