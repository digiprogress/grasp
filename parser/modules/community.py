"""
Community Detection Module

Detects code communities using Leiden algorithm at two levels:
- Domains: High-level groups (resolution=0.3)
- Communities: Detailed groups (resolution=1.0)

Uses import relationships and function co-location to build the graph.

Leiden is an improved version of Louvain with:
- Guaranteed well-connected communities
- More stable results
- Same performance
"""

import logging
from collections import defaultdict
from typing import Dict, List, Any, Tuple, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:
    import networkx as nx
    import igraph as ig
    import leidenalg
    COMMUNITY_DETECTION_AVAILABLE = True
except ImportError as e:
    COMMUNITY_DETECTION_AVAILABLE = False
    logger.warning(f"Community detection dependencies not available: {e}")


@dataclass
class CommunityResult:
    """Result from community detection."""
    domains: List[Dict[str, Any]] = field(default_factory=list)
    communities: List[Dict[str, Any]] = field(default_factory=list)
    file_to_community: Dict[str, int] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: Optional[str] = None


class CommunityDetector:
    """Multi-level community detection using Leiden algorithm."""

    def __init__(
        self,
        domain_resolution: float = 0.3,
        community_resolution: float = 1.0,
        min_community_size: int = 2
    ):
        self.domain_resolution = domain_resolution
        self.community_resolution = community_resolution
        self.min_community_size = min_community_size

    def detect(self, parsed_results: List[Dict[str, Any]]) -> CommunityResult:
        """
        Detect communities from parsed code data.

        Args:
            parsed_results: List of ParseResult-like dicts with:
                - file_path: str
                - functions: List[Dict]
                - imports: List[Dict]

        Returns:
            CommunityResult with domains, communities, and mappings
        """
        if not COMMUNITY_DETECTION_AVAILABLE:
            return CommunityResult(
                success=False,
                error="igraph/leidenalg not installed"
            )

        if not parsed_results:
            return CommunityResult(stats={"nodes": 0, "edges": 0})

        # Build graph from parsed results
        G, func_to_file = self._build_graph(parsed_results)

        if G.number_of_nodes() == 0:
            return CommunityResult(stats={"nodes": 0, "edges": 0})

        logger.info(f"Built graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

        # Detect at two levels
        logger.info(f"Running Leiden for domains (resolution={self.domain_resolution})...")
        domains = self._detect_level(G, self.domain_resolution, "domain")
        logger.info(f"Running Leiden for communities (resolution={self.community_resolution})...")
        communities = self._detect_level(G, self.community_resolution, "community")

        # Link communities to parent domains
        communities = self._link_to_parents(communities, domains)

        # Build file->community mapping
        file_to_community = {}
        for comm in communities:
            for file_path in comm.get("files", []):
                if file_path not in file_to_community:
                    file_to_community[file_path] = comm["id"]

        # Calculate modularity using igraph
        try:
            ig_graph = ig.Graph.from_networkx(G)
            membership = [0] * ig_graph.vcount()
            node_to_idx = {name: idx for idx, name in enumerate(ig_graph.vs["_nx_name"])}
            for comm in communities:
                for member in comm["members"]:
                    if member in node_to_idx:
                        membership[node_to_idx[member]] = comm["id"]
            modularity = ig_graph.modularity(membership) if communities else 0
        except Exception:
            modularity = 0

        return CommunityResult(
            domains=domains,
            communities=communities,
            file_to_community=file_to_community,
            stats={
                "nodes": G.number_of_nodes(),
                "edges": G.number_of_edges(),
                "domain_count": len(domains),
                "community_count": len(communities),
                "modularity": modularity
            },
            success=True
        )

    def _build_graph(self, parsed_results: List[Dict]) -> Tuple["nx.Graph", Dict[str, str]]:
        """Build NetworkX graph from parsed code data."""
        G = nx.Graph()
        func_to_file = {}
        file_functions = defaultdict(list)

        # Add function AND method nodes
        for result in parsed_results:
            file_path = result.get("file_path", "")

            # Standalone functions
            for func in result.get("functions", []):
                func_name = func.get("name")
                if func_name:
                    node_id = f"{file_path}:{func_name}"
                    G.add_node(node_id, type="function", file=file_path, name=func_name)
                    func_to_file[node_id] = file_path
                    file_functions[file_path].append(node_id)

            # Class methods (treat as functions for community detection)
            for method in result.get("methods", []):
                method_name = method.get("name")
                class_name = method.get("class_name", "")
                if method_name:
                    node_id = f"{file_path}:{class_name}.{method_name}"
                    G.add_node(node_id, type="method", file=file_path, name=method_name, class_name=class_name)
                    func_to_file[node_id] = file_path
                    file_functions[file_path].append(node_id)

        # Edge: functions in same file (weak)
        for file_path, funcs in file_functions.items():
            for i, func1 in enumerate(funcs):
                for func2 in funcs[i+1:]:
                    G.add_edge(func1, func2, type="same_file", weight=0.5)

        logger.info(f"Community graph: {G.number_of_nodes()} nodes from {len(file_functions)} files")

        # Build lookup: filename stem -> list of file paths (O(1) instead of O(n) scan)
        file_stem_map = defaultdict(list)
        for fp in file_functions:
            stem = fp.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            file_stem_map[stem].append(fp)

        # Edge: import relationships (stronger) — O(files * imports) with stem lookup
        for result in parsed_results:
            file_path = result.get("file_path", "")
            imports = result.get("imports", [])
            file_funcs = file_functions.get(file_path, [])

            for imp in imports:
                source = imp.get("module", "")
                if not source:
                    continue
                # Extract stem from import source for O(1) lookup
                stem = source.rsplit("/", 1)[-1].rsplit(".", 1)[0] if source else ""
                matched_files = file_stem_map.get(stem, [])
                for other_file in matched_files:
                    if other_file != file_path:
                        other_funcs = file_functions.get(other_file, [])
                        for func in file_funcs[:5]:
                            for other_func in other_funcs[:5]:
                                if not G.has_edge(func, other_func):
                                    G.add_edge(func, other_func, type="imports", weight=0.7)

        logger.info(f"Community graph: {G.number_of_edges()} edges after import phase")

        # Build lookup: function/method name -> list of node IDs (O(1) callee lookup)
        func_name_map = defaultdict(list)
        for fp, funcs in file_functions.items():
            for node_id in funcs:
                # node_id is "file:name" or "file:Class.name" — extract the tail
                tail = node_id.split(":", 1)[-1] if ":" in node_id else node_id
                # Index by full tail and by method name after dot
                func_name_map[tail].append(node_id)
                if "." in tail:
                    func_name_map[tail.split(".")[-1]].append(node_id)

        return G, func_to_file

    def _detect_level(self, G: "nx.Graph", resolution: float, level: str) -> List[Dict]:
        """Detect communities at a specific resolution level using Leiden algorithm."""
        try:
            # Convert NetworkX graph to igraph
            ig_graph = ig.Graph.from_networkx(G)

            # Run Leiden algorithm
            partition = leidenalg.find_partition(
                ig_graph,
                leidenalg.RBConfigurationVertexPartition,
                resolution_parameter=resolution,
                seed=42
            )

            # Convert back to sets of node names
            raw = [set(ig_graph.vs[list(part)]["_nx_name"]) for part in partition]
        except Exception as e:
            logger.error(f"Leiden failed at {level}: {e}")
            return []

        results = []
        for i, members_set in enumerate(raw):
            members = sorted(list(members_set))
            if len(members) < self.min_community_size:
                continue

            files = self._get_files(members, G)
            name = self._suggest_name(members, files)

            results.append({
                "id": i,
                "level": level,
                "suggested_name": name,
                "members": members,
                "files": files,
                "size": len(members),
                "file_count": len(files)
            })

        # Sort by size, renumber
        results.sort(key=lambda x: x["size"], reverse=True)
        for i, r in enumerate(results):
            r["id"] = i

        return results

    def _get_files(self, members: List[str], G: "nx.Graph") -> List[str]:
        """Get unique files for community members."""
        files = set()
        for member in members:
            if member in G.nodes:
                f = G.nodes[member].get("file")
                if f:
                    files.add(f)
        return sorted(files)

    def _suggest_name(self, members: List[str], files: List[str]) -> str:
        """Suggest a name based on common path prefix and distinguishing segments."""
        if not files:
            return "unknown"

        split = [f.split("/") for f in files]
        common = []
        if split:
            min_len = min(len(f) for f in split)
            for i in range(min_len):
                parts = set(f[i] for f in split)
                if len(parts) == 1:
                    common.append(list(parts)[0])
                else:
                    break

        if common:
            prefix = "/".join(common[-2:])
            # For single-file domains, use parent dir + filename (without extension)
            if len(files) == 1:
                parts = files[0].rsplit("/", 1)
                fname = parts[-1].rsplit(".", 1)[0]
                parent = parts[0].split("/")[-1] if "/" in parts[0] else parts[0]
                return f"{parent}/{fname}"
            return prefix

        # No common prefix — find the most common directory among files
        from collections import Counter
        dirs = [f.rsplit("/", 1)[0] if "/" in f else "" for f in files]
        if dirs:
            most_common_dir = Counter(dirs).most_common(1)[0][0]
            if most_common_dir:
                return most_common_dir.split("/")[-1]

        return "unknown"

    def _link_to_parents(self, communities: List[Dict], domains: List[Dict]) -> List[Dict]:
        """Link each community to its parent domain."""
        domain_members = {}
        for d in domains:
            for m in d["members"]:
                domain_members[m] = d["id"]

        for comm in communities:
            votes = defaultdict(int)
            for m in comm["members"]:
                if m in domain_members:
                    votes[domain_members[m]] += 1
            comm["parent_domain"] = max(votes, key=votes.get) if votes else 0

        return communities
