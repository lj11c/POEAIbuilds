"""
Passive tree pathfinding engine.

Parses the tree.lua graph and computes the shortest connected tree
from a class start node through a set of target notable/keystone nodes.

Uses a weighted A* Steiner-tree approximation:
  1. Parse node positions from group/orbit/orbitIndex data in tree.lua
  2. Score each node by stat relevance to the build (lower cost = more desirable)
  3. Use A* (Euclidean distance heuristic + stat-based node costs) to find paths
  4. Connect targets in greedy nearest-first order
  5. Pad to 123 points using centroid-proximity + stat scoring
"""

import re
import os
import math
import heapq
import logging
from collections import deque

# ── Orbit geometry constants (from POB PassiveTree.lua) ──────────────────────
# Number of node slots per orbit ring (orbit index 0-6)
SKILLS_PER_ORBIT = [1, 6, 16, 16, 40, 72, 72]
# Tree-space radius (pixels) for each orbit ring
ORBIT_RADII = [0, 82, 162, 335, 493, 662, 846]
# Non-uniform placement angles (degrees) for 16-node orbits
ORBIT_ANGLES_16 = [
    0, 30, 45, 60, 90, 120, 135, 150,
    180, 210, 225, 240, 270, 300, 315, 330,
]
# Non-uniform placement angles (degrees) for 40-node orbits
ORBIT_ANGLES_40 = [
    0, 10, 20, 30, 40, 45, 50, 60, 70, 80, 90, 100, 110, 120, 130, 135,
    140, 150, 160, 170, 180, 190, 200, 210, 220, 225, 230, 240, 250, 260,
    270, 280, 290, 300, 310, 315, 320, 330, 340, 350,
]
# A* heuristic divisor — rough max Euclidean distance covered per hop
_ASTAR_HEURISTIC_SCALE = 500.0


def _orbit_angle_deg(orbit: int, orbit_index: int) -> float:
    """Return placement angle in degrees for a node at (orbit, orbit_index)."""
    n = SKILLS_PER_ORBIT[orbit] if orbit < len(SKILLS_PER_ORBIT) else 1
    if n == 16:
        return float(ORBIT_ANGLES_16[orbit_index % 16])
    if n == 40:
        return float(ORBIT_ANGLES_40[orbit_index % 40])
    return (360.0 * orbit_index / n) if n > 0 else 0.0

logger = logging.getLogger(__name__)

POB_DIR = os.path.join(os.path.dirname(__file__), "POB", "PathOfBuilding-dev")
TREE_LUA_PATH = os.path.join(POB_DIR, "src", "TreeData", "3_28", "tree.lua")


class PassiveTree:
    """In-memory passive tree graph with pathfinding."""

    def __init__(self):
        # node_id (int) → set of neighbor node_ids (int)
        self.graph: dict[int, set[int]] = {}
        # node_id → {name, is_notable, is_keystone, is_mastery, ascendancy,
        #             class_start_index, group, orbit, orbit_index, stats_text}
        self.node_info: dict[int, dict] = {}
        # lowercase name → list of node_ids
        self.name_to_ids: dict[str, list[int]] = {}
        # classStartIndex → node_id
        self.class_starts: dict[int, int] = {}
        # set of jewel socket node IDs
        self.jewel_sockets: set[int] = set()
        # group_id → {"x": float, "y": float}
        self.groups: dict[int, dict] = {}
        # node_id → (x, y) in tree-space coordinates
        self.node_positions: dict[int, tuple[float, float]] = {}
        # class name (e.g. "Witch") → classStartIndex
        self.class_name_to_index: dict[str, int] = {
            "Scion": 0, "Marauder": 1, "Ranger": 2,
            "Witch": 3, "Duelist": 4, "Templar": 5, "Shadow": 6,
        }

    def load(self, filepath: str = TREE_LUA_PATH):
        """Parse tree.lua and build the graph."""
        logger.info(f"Loading passive tree from {filepath}...")
        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        # Parse group center coordinates first (needed for position computation)
        self._parse_groups(content)

        # Find the main ["nodes"] section (rfind skips ["nodes"] inside groups)
        idx = content.rfind('["nodes"]=')
        if idx == -1:
            raise ValueError("Could not find nodes section in tree.lua")

        brace_start = content.index("{", idx)
        self._parse_nodes(content, brace_start)

        # Compute tree-space x/y for every node using group + orbit geometry
        self._compute_node_positions()

        logger.info(f"Tree loaded: {len(self.node_info)} nodes, "
                    f"{len(self.class_starts)} class starts, "
                    f"{len(self.node_positions)} positioned, "
                    f"{len(self.name_to_ids)} unique names")

    def _parse_groups(self, content: str):
        """Parse the top-level ['groups'] table to get each group's center x/y."""
        idx = content.find('["groups"]=')
        if idx == -1:
            logger.warning("No ['groups'] section found — node positions unavailable")
            return

        brace_start = content.index("{", idx)
        _, groups_end = self._extract_block(content, brace_start)
        groups_section = content[brace_start: groups_end + 1]

        group_id_re = re.compile(r'\[(\d+)\]=\s*\{')
        pos = 1  # skip the opening brace of the outer table

        while pos < len(groups_section):
            m = group_id_re.search(groups_section, pos)
            if not m:
                break

            group_id = int(m.group(1))
            entry_start = groups_section.index("{", m.start())
            entry_block, entry_end = self._extract_block(groups_section, entry_start)

            x_m = re.search(r'"x"\]\s*=\s*(-?\d+(?:\.\d+)?)', entry_block)
            y_m = re.search(r'"y"\]\s*=\s*(-?\d+(?:\.\d+)?)', entry_block)
            if x_m and y_m:
                self.groups[group_id] = {
                    "x": float(x_m.group(1)),
                    "y": float(y_m.group(1)),
                }

            pos = entry_end + 1

        logger.info(f"Parsed {len(self.groups)} groups")

    def _compute_node_positions(self):
        """
        Compute tree-space (x, y) for every node using:
            x = group.x + sin(angle) * orbitRadius
            y = group.y - cos(angle) * orbitRadius
        Mirrors the calculation in POB's PassiveTree.lua ProcessNode().
        """
        for nid, info in self.node_info.items():
            group_id = info.get("group")
            if group_id is None or group_id not in self.groups:
                continue
            orbit = info.get("orbit", 0)
            orbit_index = info.get("orbit_index", 0)
            radius = ORBIT_RADII[orbit] if orbit < len(ORBIT_RADII) else 0
            angle_rad = math.radians(_orbit_angle_deg(orbit, orbit_index))
            gx = self.groups[group_id]["x"]
            gy = self.groups[group_id]["y"]
            self.node_positions[nid] = (
                gx + math.sin(angle_rad) * radius,
                gy - math.cos(angle_rad) * radius,
            )

    def _parse_nodes(self, content: str, start: int):
        """Parse all nodes from the nodes table."""
        pos = start + 1
        content_len = len(content)
        node_id_re = re.compile(r"\[(\d+)\]=\s*\{")

        while pos < content_len:
            # Skip whitespace
            while pos < content_len and content[pos] in " \t\n\r":
                pos += 1
            if pos >= content_len or content[pos] == "}":
                break

            # Skip the special "root" node (it's not allocatable)
            if content[pos: pos + 7] == '["root"':
                # But still parse its out-edges for connectivity
                block_start = content.index("{", pos)
                block, block_end = self._extract_block(content, block_start)
                out_ids = self._parse_edge_list(block, "out")
                # root connects to class starts — store this info
                self.graph.setdefault(-1, set()).update(out_ids)
                for oid in out_ids:
                    self.graph.setdefault(oid, set()).add(-1)
                pos = block_end + 1
                continue

            m = node_id_re.match(content, pos)
            if not m:
                pos += 1
                continue

            node_id = int(m.group(1))
            block_start = content.index("{", pos)
            block, block_end = self._extract_block(content, block_start)

            # Parse node properties
            # Lua format: ["key"]= value — note the ] before =
            name_m = re.search(r'"name"\]\s*=\s*"([^"]+)"', block)
            if not name_m:
                pos = block_end + 1
                continue

            name = name_m.group(1)
            is_notable = '"isNotable"]= true' in block
            is_keystone = '"isKeystone"]= true' in block
            is_mastery = '"isMastery"]= true' in block
            is_jewel_socket = '"isJewelSocket"]= true' in block
            is_ascendancy_start = '"isAscendancyStart"]= true' in block
            asc_m = re.search(r'"ascendancyName"\]\s*=\s*"([^"]+)"', block)
            ascendancy = asc_m.group(1) if asc_m else None
            csi_m = re.search(r'"classStartIndex"\]\s*=\s*(\d+)', block)
            class_start_index = int(csi_m.group(1)) if csi_m else None

            # Extract stat text strings
            stats_m = re.search(r'"stats"\]\s*=\s*\{([^}]*)\}', block)
            stats_text = ""
            if stats_m:
                stat_strings = re.findall(r'"([^"]+)"', stats_m.group(1))
                stats_text = " | ".join(stat_strings).lower()

            # Spatial placement data (used to compute x/y positions)
            group_m = re.search(r'"group"\]\s*=\s*(\d+)', block)
            group_id = int(group_m.group(1)) if group_m else None
            orbit_m = re.search(r'"orbit"\]\s*=\s*(\d+)', block)
            orbit = int(orbit_m.group(1)) if orbit_m else 0
            orbit_idx_m = re.search(r'"orbitIndex"\]\s*=\s*(\d+)', block)
            orbit_index = int(orbit_idx_m.group(1)) if orbit_idx_m else 0

            # Track jewel sockets
            if is_jewel_socket:
                self.jewel_sockets.add(node_id)

            # Store node info
            self.node_info[node_id] = {
                "name": name,
                "is_notable": is_notable,
                "is_keystone": is_keystone,
                "is_mastery": is_mastery,
                "is_jewel_socket": is_jewel_socket,
                "is_ascendancy_start": is_ascendancy_start,
                "ascendancy": ascendancy,
                "class_start_index": class_start_index,
                "stats_text": stats_text,
                "group": group_id,
                "orbit": orbit,
                "orbit_index": orbit_index,
            }

            # Index by name
            key = name.lower()
            self.name_to_ids.setdefault(key, []).append(node_id)

            # Track class starts
            if class_start_index is not None:
                self.class_starts[class_start_index] = node_id

            # Parse edges (both in and out — we build an undirected graph)
            out_ids = self._parse_edge_list(block, "out")
            in_ids = self._parse_edge_list(block, "in")
            neighbors = out_ids | in_ids

            self.graph.setdefault(node_id, set()).update(neighbors)
            for nid in neighbors:
                self.graph.setdefault(nid, set()).add(node_id)

            pos = block_end + 1

    def _extract_block(self, content: str, start: int) -> tuple[str, int]:
        """Extract a brace-delimited block and return (block_text, end_pos)."""
        depth = 0
        pos = start
        while pos < len(content):
            if content[pos] == "{":
                depth += 1
            elif content[pos] == "}":
                depth -= 1
                if depth == 0:
                    return content[start: pos + 1], pos
            pos += 1
        return content[start:], len(content) - 1

    def _parse_edge_list(self, block: str, key: str) -> set[int]:
        """Extract node IDs from an edge list like ["out"]= {"123", "456"}."""
        pattern = rf'"{key}"\]\s*=\s*\{{([^}}]*)\}}'
        m = re.search(pattern, block)
        if not m:
            return set()
        return {int(x) for x in re.findall(r'"(\d+)"', m.group(1))}

    def resolve_names(self, names: list[str]) -> tuple[list[int], list[str], list[str]]:
        """
        Resolve a list of notable/keystone names to node IDs.
        Returns (found_ids, matched_names, unmatched_names).
        Only considers main-tree nodes (not ascendancy nodes).
        """
        found: list[int] = []
        matched: list[str] = []
        unmatched: list[str] = []

        for name in names:
            key = name.lower().strip()
            candidates = self.name_to_ids.get(key, [])

            # Fuzzy fallback: startswith or contains
            if not candidates:
                for node_key, ids in self.name_to_ids.items():
                    if key in node_key or node_key in key:
                        candidates = ids
                        break

            # Only use main-tree (non-ascendancy) nodes
            main = [nid for nid in candidates
                    if not self.node_info[nid].get("ascendancy")]
            chosen = main[0] if main else None

            if chosen:
                found.append(chosen)
                matched.append(f"{name} → #{chosen}")
            else:
                unmatched.append(name)

        return found, matched, unmatched

    def get_ascendancy_start_node(self, ascendancy_name: str) -> int | None:
        """Find the start node for an ascendancy (the node with isAscendancyStart)."""
        asc_lower = ascendancy_name.lower().strip()
        for nid, info in self.node_info.items():
            if (info.get("ascendancy") or "").lower() == asc_lower and \
               info.get("name", "").lower() == asc_lower:
                return nid
        # Fallback: look for isAscendancyStart flag
        for nid, info in self.node_info.items():
            if (info.get("ascendancy") or "").lower() == asc_lower and \
               info.get("is_ascendancy_start"):
                return nid
        return None

    def get_ascendancy_notables(self, ascendancy_name: str) -> list[int]:
        """Get all notable node IDs for an ascendancy."""
        asc_lower = ascendancy_name.lower().strip()
        return [nid for nid, info in self.node_info.items()
                if (info.get("ascendancy") or "").lower() == asc_lower
                and info.get("is_notable")]

    def rank_ascendancy_notable(self, node_id: int,
                                 damage_type: str = "",
                                 weapon_type: str = "",
                                 attack_style: str = "") -> int:
        """
        Score an ascendancy notable for build relevance (lower = better fit).
        Looks at the node's stats text and matches against build parameters.
        """
        info = self.node_info.get(node_id, {})
        stats = info.get("stats_text", "").lower()
        name = info.get("name", "").lower()
        score = 50  # neutral baseline

        damage_type = damage_type.lower()
        weapon_type = weapon_type.lower()
        attack_style = attack_style.lower()

        # ── Damage type match ──
        if damage_type:
            if "fire" in damage_type:
                if "fire" in stats: score -= 20
                if "cold" in stats and "fire" not in stats: score += 10
                if "lightning" in stats and "fire" not in stats: score += 10
            elif "cold" in damage_type:
                if "cold" in stats: score -= 20
                if "fire" in stats and "cold" not in stats: score += 10
            elif "lightning" in damage_type:
                if "lightning" in stats: score -= 20
            elif "chaos" in damage_type:
                if "chaos" in stats: score -= 20
            elif "physical" in damage_type:
                if "physical" in stats: score -= 20
            # Elemental generics
            if "elemental" in damage_type or damage_type in ("fire", "cold", "lightning"):
                if "elemental" in stats: score -= 15

        # ── Attack vs spell ──
        if "spell" in attack_style or "caster" in attack_style:
            if "spell" in stats or "cast" in stats: score -= 15
            if "attack" in stats and "spell" not in stats: score += 15
            if "melee" in stats: score += 20
        elif "attack" in attack_style or weapon_type:
            if "attack" in stats: score -= 10
            if "spell" in stats and "attack" not in stats: score += 15
            if "melee" in stats and ("melee" in weapon_type or "sword" in weapon_type
                                      or "axe" in weapon_type or "mace" in weapon_type
                                      or "claw" in weapon_type or "dagger" in weapon_type):
                score -= 10

        # ── Weapon type ──
        if weapon_type:
            if "bow" in weapon_type and "bow" in stats: score -= 10
            if "wand" in weapon_type and "wand" in stats: score -= 10
            if "two-handed" in weapon_type or "two handed" in weapon_type:
                if "two-handed" in stats or "two handed" in stats: score -= 10

        # ── Universal good stats (always useful) ──
        universal = ["damage", "life", "energy shield", "resistance", "leech",
                     "speed", "critical", "armour", "evasion"]
        for u in universal:
            if u in stats:
                score -= 3

        # ── Penalize very niche stats ──
        niche = ["minion", "totem", "trap", "mine", "brand", "herald"]
        for n in niche:
            # Only penalize if the build doesn't use this mechanic
            if n in stats:
                if n not in damage_type and n not in attack_style:
                    score += 20

        return score

    def auto_select_ascendancy(self, ascendancy_name: str,
                                damage_type: str = "",
                                weapon_type: str = "",
                                attack_style: str = "",
                                max_points: int = 8) -> tuple[list[int], list[str]]:
        """
        Automatically select and allocate the best ascendancy notables
        for a build, filling up to max_points (default 8).

        The ascendancy start node is free. Each other node costs 1 point.
        Notables are ranked by relevance to the build's damage/weapon/style,
        then greedily allocated from best to worst until points run out.

        Returns (allocated_node_ids, matched_descriptions).
        """
        start_id = self.get_ascendancy_start_node(ascendancy_name)
        if start_id is None:
            logger.warning(f"No ascendancy start node for '{ascendancy_name}'")
            return [], []

        asc_lower = ascendancy_name.lower().strip()

        # Get all notables and rank them
        notable_ids = self.get_ascendancy_notables(ascendancy_name)
        ranked = []
        for nid in notable_ids:
            score = self.rank_ascendancy_notable(nid, damage_type, weapon_type, attack_style)
            name = self.node_info[nid]["name"]
            ranked.append((score, nid, name))
        ranked.sort(key=lambda x: x[0])  # best first

        logger.info(f"Ascendancy '{ascendancy_name}' notables ranked: "
                     + ", ".join(f"{name}({score})" for score, _, name in ranked))

        # Greedily allocate best notables until we hit the point cap
        allocated: set[int] = {start_id}  # free start node
        matched: list[str] = []

        for score, nid, name in ranked:
            # Path from current allocated set to this notable
            path = self._bfs_ascendancy(allocated, {nid}, asc_lower)
            if path is None:
                logger.warning(f"Cannot reach ascendancy notable '{name}' — skipping")
                continue

            # Check point cost
            new_nodes = [n for n in path if n not in allocated]
            points_after = (len(allocated) - 1) + len(new_nodes)

            if points_after > max_points:
                logger.info(f"Ascendancy cap: skipping '{name}' "
                            f"(would use {points_after}/{max_points} points)")
                continue

            allocated.update(path)
            matched.append(f"{name} → #{nid} (ascendancy)")

        points_used = len(allocated) - 1
        logger.info(f"Ascendancy '{ascendancy_name}': auto-selected {len(matched)} notables, "
                     f"{points_used}/{max_points} points used")
        return sorted(allocated), matched

    def _bfs_ascendancy(self, sources: set[int], targets: set[int],
                         ascendancy_name: str) -> list[int] | None:
        """
        Multi-source BFS within an ascendancy sub-graph only.
        Only traverses nodes belonging to the named ascendancy.
        """
        visited: dict[int, int | None] = {}
        queue: deque[int] = deque()

        for s in sources:
            visited[s] = None
            queue.append(s)

        while queue:
            current = queue.popleft()
            if current in targets:
                path = []
                node = current
                while node is not None:
                    path.append(node)
                    node = visited[node]
                path.reverse()
                return path

            for neighbor in self.graph.get(current, set()):
                if neighbor in visited:
                    continue
                info = self.node_info.get(neighbor)
                if not info:
                    continue
                # Only traverse nodes in this ascendancy
                if (info.get("ascendancy") or "").lower() != ascendancy_name:
                    continue
                # Skip mastery nodes
                if info.get("is_mastery"):
                    continue
                visited[neighbor] = current
                queue.append(neighbor)

        return None

    def bfs_shortest_path(self, start: int, target: int,
                          excluded: set[int] | None = None) -> list[int] | None:
        """
        BFS from start to target, returning the path as a list of node IDs.
        Skips ascendancy nodes and mastery nodes.
        Returns None if no path exists.
        """
        if start == target:
            return [start]

        excluded = excluded or set()
        visited = {start}
        queue = deque([(start, [start])])

        while queue:
            current, path = queue.popleft()
            for neighbor in self.graph.get(current, set()):
                if neighbor in visited or neighbor in excluded:
                    continue
                # Skip ascendancy nodes (they're in a separate sub-graph)
                info = self.node_info.get(neighbor)
                if info and info.get("ascendancy"):
                    continue
                # Skip mastery nodes (not allocatable)
                if info and info.get("is_mastery"):
                    continue

                new_path = path + [neighbor]
                if neighbor == target:
                    return new_path
                visited.add(neighbor)
                queue.append((neighbor, new_path))

        return None

    def find_allocated_jewel_sockets(self, allocated_nodes: set[int]) -> list[int]:
        """
        Find jewel socket nodes that are already allocated or are
        directly adjacent to an allocated node (1 hop away).
        Returns sorted list of jewel socket node IDs.
        """
        sockets = []
        for sid in self.jewel_sockets:
            if sid in allocated_nodes:
                sockets.append(sid)
            else:
                # Check if any neighbor is allocated (1 hop)
                neighbors = self.graph.get(sid, set())
                if neighbors & allocated_nodes:
                    sockets.append(sid)
        return sorted(sockets)

    def compute_conflict_nodes(self, weapon_type: str = "",
                               attack_style: str = "") -> set[int]:
        """
        Find small passive nodes whose stats conflict with the build's
        weapon type and attack style. Only flags non-notable, non-keystone
        nodes so that intentional target selections are never blocked.
        """
        weapon_type = weapon_type.lower()
        attack_style = attack_style.lower()
        conflict_keywords: list[str] = []

        # ── Weapon-type conflicts ─────────────────────────────────────────
        # Each entry lists stat-text substrings that indicate a node is useless
        # (or harmful) for a build using this weapon type.
        # Covers both small-passive phrasing ("with staves") and
        # notable/keystone phrasing ("staff attacks", "wielding a staff").
        WEAPON_CONFLICTS = {
            "sword":  ["with axes", "axe attacks", "wielding an axe",
                       "with maces", "mace attacks", "wielding a mace",
                       "with claws", "claw attacks", "wielding a claw",
                       "with daggers", "dagger attacks", "wielding a dagger",
                       "with bows", "bow attacks", "wielding a bow",
                       "with wands", "wand attacks", "wielding a wand",
                       "with staves", "staff attacks", "wielding a staff"],
            "axe":    ["with swords", "sword attacks", "wielding a sword",
                       "with maces", "mace attacks", "wielding a mace",
                       "with claws", "claw attacks", "wielding a claw",
                       "with daggers", "dagger attacks", "wielding a dagger",
                       "with bows", "bow attacks", "wielding a bow",
                       "with wands", "wand attacks", "wielding a wand",
                       "with staves", "staff attacks", "wielding a staff"],
            "mace":   ["with swords", "sword attacks", "wielding a sword",
                       "with axes", "axe attacks", "wielding an axe",
                       "with claws", "claw attacks", "wielding a claw",
                       "with daggers", "dagger attacks", "wielding a dagger",
                       "with bows", "bow attacks", "wielding a bow",
                       "with wands", "wand attacks", "wielding a wand",
                       "with staves", "staff attacks", "wielding a staff"],
            "claw":   ["with swords", "sword attacks", "wielding a sword",
                       "with axes", "axe attacks", "wielding an axe",
                       "with maces", "mace attacks", "wielding a mace",
                       "with daggers", "dagger attacks", "wielding a dagger",
                       "with bows", "bow attacks", "wielding a bow",
                       "with wands", "wand attacks", "wielding a wand",
                       "with staves", "staff attacks", "wielding a staff"],
            "dagger": ["with swords", "sword attacks", "wielding a sword",
                       "with axes", "axe attacks", "wielding an axe",
                       "with maces", "mace attacks", "wielding a mace",
                       "with claws", "claw attacks", "wielding a claw",
                       "with bows", "bow attacks", "wielding a bow",
                       "with wands", "wand attacks", "wielding a wand",
                       "with staves", "staff attacks", "wielding a staff"],
            "bow":    ["with swords", "sword attacks", "wielding a sword",
                       "with axes", "axe attacks", "wielding an axe",
                       "with maces", "mace attacks", "wielding a mace",
                       "with claws", "claw attacks", "wielding a claw",
                       "with daggers", "dagger attacks", "wielding a dagger",
                       "with wands", "wand attacks", "wielding a wand",
                       "with staves", "staff attacks", "wielding a staff",
                       "while holding a shield", "while dual wielding"],
            "wand":   ["with swords", "sword attacks", "wielding a sword",
                       "with axes", "axe attacks", "wielding an axe",
                       "with maces", "mace attacks", "wielding a mace",
                       "with claws", "claw attacks", "wielding a claw",
                       "with daggers", "dagger attacks", "wielding a dagger",
                       "with bows", "bow attacks", "wielding a bow",
                       "with staves", "staff attacks", "wielding a staff"],
            "staff":  ["with swords", "sword attacks", "wielding a sword",
                       "with axes", "axe attacks", "wielding an axe",
                       "with maces", "mace attacks", "wielding a mace",
                       "with claws", "claw attacks", "wielding a claw",
                       "with daggers", "dagger attacks", "wielding a dagger",
                       "with bows", "bow attacks", "wielding a bow",
                       "with wands", "wand attacks", "wielding a wand",
                       "while holding a shield", "while dual wielding"],
        }
        for wtype, conflicts in WEAPON_CONFLICTS.items():
            if wtype in weapon_type:
                conflict_keywords.extend(conflicts)
                break

        # ── Attack-style conflicts ────────────────────────────────────────
        if "two-handed" in attack_style or "two handed" in attack_style:
            conflict_keywords.extend([
                "while holding a shield", "while dual wielding",
                "block chance with shield",
            ])
        elif "dual" in attack_style:
            conflict_keywords.extend([
                "while holding a shield", "two handed weapon",
                "two-handed", "block chance with shield",
            ])
        elif "shield" in attack_style:
            conflict_keywords.extend([
                "while dual wielding", "two handed weapon", "two-handed",
            ])

        if not conflict_keywords:
            return set()

        # De-duplicate
        conflict_keywords = list(set(k.lower() for k in conflict_keywords))

        # Scan all main-tree nodes (including notables) for conflicting stats.
        # Explicitly-requested target notables are exempted in compute_build_tree.
        conflicting: set[int] = set()
        for nid, info in self.node_info.items():
            if info.get("is_mastery") or info.get("ascendancy"):
                continue
            stats = info.get("stats_text", "")
            if not stats:
                continue
            for keyword in conflict_keywords:
                if keyword in stats:
                    conflicting.add(nid)
                    break

        logger.info(f"Conflict filter: {len(conflicting)} small passives forbidden "
                     f"(weapon={weapon_type}, style={attack_style})")
        return conflicting

    # ── Keystone mutual-exclusion rules ──────────────────────────────────────
    # Each entry describes a keystone that is incompatible with a class of nodes.
    #   keystone_stats   — substrings in the keystone's own stats_text
    #   conflict_stats   — substrings in stats_text of nodes that are wasted
    #   ban_from_padding — if True, also forbid conflicting nodes from padding
    #                      (use when the investment is completely wasted, e.g. crit
    #                       with RT). Set False when a small amount is still fine
    #                      (e.g. some life on a Ghost Reaver build is OK).
    _KEYSTONE_INCOMPATIBILITIES = [
        {
            "keystone_stats": [
                "never deal critical strikes",        # Resolute Technique
                "critical strikes do not deal extra", # Elemental Overload
            ],
            "conflict_stats": ["critical strike chance", "critical strike multiplier"],
            "ban_from_padding": True,
            # False = drop the keystone when conflict notables are also present
            # (crit investment is more specific; RT is the mistake)
            "keystone_takes_precedence": False,
        },
        {
            "keystone_stats": ["leech energy shield instead of life"],  # Ghost Reaver
            "conflict_stats": ["maximum life"],
            "ban_from_padding": False,
            "keystone_takes_precedence": False,  # life notables win; GR is the mistake
        },
        {
            "keystone_stats": ["maximum life becomes 1"],  # Chaos Inoculation
            "conflict_stats": ["maximum life"],
            "ban_from_padding": True,
            # True = drop the conflicting life notables; CI is the intentional choice
            "keystone_takes_precedence": True,
        },
    ]

    def _handle_keystone_conflicts(self,
                                    target_ids: list[int],
                                    matched: list[str],
                                    extra_forbidden: set[int]) -> None:
        """
        Detect mutually exclusive keystones in the target list and resolve them.

        For each incompatibility rule:
          • If both the keystone AND conflicting notables are requested → drop
            the keystone (prefer keeping the more specific stat investment).
          • If only the keystone is present and ban_from_padding is True → add
            every conflicting node on the main tree to extra_forbidden so
            pathfinding and padding never touch them.
        """
        for rule in self._KEYSTONE_INCOMPATIBILITIES:
            ks_stats = rule["keystone_stats"]
            cf_stats = rule["conflict_stats"]
            ban = rule["ban_from_padding"]

            ks_ids = [
                nid for nid in target_ids
                if any(kw in self.node_info.get(nid, {}).get("stats_text", "")
                       for kw in ks_stats)
            ]
            if not ks_ids:
                continue  # this keystone not present — skip rule

            conflict_target_ids = [
                nid for nid in target_ids
                if nid not in ks_ids  # never flag the keystone as its own conflict
                and any(kw in self.node_info.get(nid, {}).get("stats_text", "")
                        for kw in cf_stats)
            ]

            if conflict_target_ids:
                if rule.get("keystone_takes_precedence", False):
                    # Keystone is the intentional build choice — drop the conflicting notables
                    for nid in conflict_target_ids:
                        name = self.node_info.get(nid, {}).get("name", str(nid))
                        logger.warning(
                            f"Dropping '{name}' — conflicts with "
                            f"{[self.node_info.get(k,{}).get('name',str(k)) for k in ks_ids]}"
                        )
                        if nid in target_ids:
                            target_ids.remove(nid)
                        matched[:] = [m for m in matched if f"#{nid}" not in m]
                else:
                    # Conflicting investment is more specific — drop the keystone
                    for nid in ks_ids:
                        name = self.node_info.get(nid, {}).get("name", str(nid))
                        logger.warning(
                            f"Dropping '{name}' — conflicts with other notables in target list"
                        )
                        target_ids.remove(nid)
                        matched[:] = [m for m in matched if f"#{nid}" not in m]
                    continue  # keystone dropped — no need to ban padding nodes

            # Keystone remains active — ban conflicting nodes from padding if required
            if ban:
                banned = 0
                for nid, info in self.node_info.items():
                    if info.get("ascendancy") or info.get("is_mastery"):
                        continue
                    if nid in ks_ids:
                        continue  # never ban the keystone itself
                    if any(kw in info.get("stats_text", "") for kw in cf_stats):
                        extra_forbidden.add(nid)
                        banned += 1
                ks_names = [self.node_info.get(n, {}).get("name", str(n)) for n in ks_ids]
                logger.info(
                    f"Keystone conflict: {ks_names} active — "
                    f"banned {banned} incompatible nodes from pathfinding and padding"
                )

    def _node_traversal_cost(self, node_id: int,
                              damage_type: str = "",
                              weapon_type: str = "",
                              attack_style: str = "") -> float:
        """
        Return the A* traversal cost for passing through this node.
        Lower cost = more desirable path through this node.
        Conflict nodes are forbidden upstream and never reach this function.
        """
        info = self.node_info.get(node_id, {})
        stats = info.get("stats_text", "")
        cost = 1.0
        primary_match = False

        dt = damage_type.lower()
        wt = weapon_type.lower()
        st = attack_style.lower()

        # Reward nodes whose stats match the build's damage type
        if dt and dt in stats:
            cost *= 0.5
            primary_match = True
        if "elemental" in stats and dt in ("fire", "cold", "lightning", "elemental"):
            cost *= 0.6
            primary_match = True
        # Reward weapon-type matches
        if wt and wt in stats:
            cost *= 0.65
            primary_match = True
        # Reward attack/spell style matches
        if "spell" in st or "caster" in st:
            if "spell" in stats or "cast" in stats:
                cost *= 0.6
                primary_match = True
        elif "attack" in st and "attack" in stats:
            cost *= 0.65
            primary_match = True

        # Slight preference for universally defensive/offensive stats
        # Use specific terms — "life" alone is too broad and catches leech nodes
        if any(g in stats for g in ("maximum life", "energy shield", "resistance", "critical strike")):
            cost *= 0.85

        # Notable bonus only when the notable actually has relevant stats —
        # a notable with no matching stats is no better than a small passive
        if info.get("is_notable") and primary_match:
            cost *= 0.75

        return max(cost, 0.2)

    def compute_build_tree(self, class_name: str,
                           target_names: list[str],
                           ascendancy_name: str = "",
                           damage_type: str = "",
                           weapon_type: str = "",
                           attack_style: str = "") -> tuple[list[int], list[str], list[str], list[int]]:
        """
        Compute the full set of allocated node IDs for a build,
        including both main-tree and ascendancy nodes.

        Args:
            class_name: e.g. "Witch"
            target_names: list of notable/keystone names to path to
            ascendancy_name: e.g. "Elementalist" — auto-selects best ascendancy notables
            damage_type: e.g. "Fire", "Physical" — used to rank ascendancy notables
            weapon_type: e.g. "Staff", "Two-Handed Sword" — used to filter conflicting nodes
            attack_style: e.g. "Two-Handed", "Dual-Wield" — used to filter conflicting nodes

        Returns:
            (all_node_ids, matched_names, unmatched_names, jewel_socket_ids)

        Uses a greedy Steiner tree approximation for the main tree,
        then auto-selects and paths the best ascendancy notables up to
        the 8-point cap.
        """
        # Get class start node
        class_idx = self.class_name_to_index.get(class_name, 0)
        start_id = self.class_starts.get(class_idx)
        if start_id is None:
            logger.error(f"No start node for class {class_name} (index {class_idx})")
            return [], [], target_names, []

        # Resolve target names to main-tree node IDs only
        # (ascendancy notables are handled separately via auto-selection)
        target_ids, matched, unmatched = self.resolve_names(target_names)

        # Filter out any ascendancy notable names from unmatched
        # (they'll be auto-selected, so don't report them as unmatched)
        if ascendancy_name:
            asc_lower = ascendancy_name.lower().strip()
            truly_unmatched = []
            for name in unmatched:
                key = name.lower().strip()
                candidates = self.name_to_ids.get(key, [])
                # Fuzzy fallback
                if not candidates:
                    for node_key, ids in self.name_to_ids.items():
                        if key in node_key or node_key in key:
                            candidates = ids
                            break
                is_asc = any((self.node_info[nid].get("ascendancy") or "").lower() == asc_lower
                             for nid in candidates)
                if not is_asc:
                    truly_unmatched.append(name)
            unmatched = truly_unmatched

        if not target_ids:
            if not ascendancy_name:
                logger.warning("No target nodes resolved — returning empty tree")
                return [], matched, unmatched, []

        # Forbidden set: root pseudo-node + other class start nodes +
        # small passives whose stats conflict with this build's weapon/style.
        # Conflict nodes are hard-blocked (never traversed or allocated).
        # Explicit target notables are exempted so they're always reachable.
        forbidden: set[int] = {-1}
        for cidx, nid in self.class_starts.items():
            if cidx != class_idx:
                forbidden.add(nid)

        conflict_nodes = self.compute_conflict_nodes(weapon_type, attack_style)

        # Resolve RT/EO vs crit keystone conflicts before building forbidden set.
        # This may remove RT/EO from target_ids or ban crit nodes from the tree.
        self._handle_keystone_conflicts(target_ids, matched, conflict_nodes)

        conflict_nodes -= set(target_ids)  # never block explicitly requested nodes
        forbidden.update(conflict_nodes)

        logger.info(f"Pathfinding: {class_name} (start #{start_id}) -> "
                    f"{len(target_ids)} targets ({len(unmatched)} unmatched), "
                    f"{len(conflict_nodes)} forbidden conflict nodes")

        # Greedy Steiner tree: repeatedly connect the nearest remaining target
        allocated: set[int] = {start_id}
        remaining_targets = set(target_ids)

        while remaining_targets:
            path = self._astar_to_nearest(
                allocated, remaining_targets, forbidden,
                damage_type, weapon_type, attack_style,
            )
            if path is None:
                unreachable = [self.node_info.get(t, {}).get("name", str(t))
                               for t in remaining_targets]
                logger.warning(f"Cannot reach targets: {unreachable}")
                break
            best_target = path[-1]
            allocated.update(path)
            remaining_targets.discard(best_target)

        # Collect jewel sockets adjacent to the path before padding
        nearby_sockets = self.find_allocated_jewel_sockets(allocated)
        for sid in nearby_sockets:
            if sid not in allocated:
                allocated.add(sid)

        # ── Pad main tree to exactly 123 points ───────────────────────────
        self._pad_to_target(
            allocated, forbidden, target_points=123,
            damage_type=damage_type, weapon_type=weapon_type,
            attack_style=attack_style, conflict_nodes=conflict_nodes,
        )

        # ── Ascendancy: auto-select best notables and fill 8 points ─────
        if ascendancy_name:
            asc_allocated, asc_matched = self.auto_select_ascendancy(
                ascendancy_name,
                damage_type=damage_type,
                weapon_type=weapon_type,
                attack_style=attack_style,
            )
            allocated.update(asc_allocated)
            matched.extend(asc_matched)

        # Also pick up any new jewel sockets from padding
        extra_sockets = self.find_allocated_jewel_sockets(allocated)
        for sid in extra_sockets:
            if sid not in allocated:
                allocated.add(sid)
        all_sockets = set(nearby_sockets) | set(extra_sockets)

        result = sorted(allocated)
        jewel_socket_ids = [s for s in sorted(all_sockets) if s in allocated]
        main_count = self._count_main_nodes(result)
        asc_count = sum(1 for nid in result if self.node_info.get(nid, {}).get("ascendancy"))
        logger.info(f"Pathfinding complete: {len(result)} total nodes "
                     f"({main_count} main tree + {asc_count} ascendancy), "
                     f"{len(jewel_socket_ids)} jewel sockets")
        return result, matched, unmatched, jewel_socket_ids

    def _count_main_nodes(self, node_ids: list[int] | set[int]) -> int:
        """Count allocatable main-tree nodes (no ascendancy, no mastery)."""
        count = 0
        for nid in node_ids:
            info = self.node_info.get(nid, {})
            if info.get("ascendancy") or info.get("is_mastery"):
                continue
            count += 1
        return count

    def _pad_to_target(self, allocated: set[int], forbidden: set[int],
                        target_points: int = 123,
                        damage_type: str = "",
                        weapon_type: str = "",
                        attack_style: str = "",
                        conflict_nodes: set[int] | None = None):
        """
        Expand the allocated tree to target_points main-tree nodes.

        Candidates are gathered by BFS from the frontier, then ranked by:
          1. Distance to the centroid of already-allocated notables
             (keeps padding within the build's active region)
          2. Stat relevance score (reuses _node_traversal_cost)
          3. Node type bonus (notables > jewel sockets > small passives)
        """
        current = self._count_main_nodes(allocated)
        if current >= target_points:
            logger.info(f"Tree already has {current}/{target_points} main-tree points — no padding needed")
            return

        needed = target_points - current
        logger.info(f"Padding tree: {current} → {target_points} ({needed} nodes to add)")

        # Centroid of allocated notables (fallback: all allocated positioned nodes)
        notable_positions = [
            self.node_positions[n] for n in allocated
            if n in self.node_positions and self.node_info.get(n, {}).get("is_notable")
        ]
        if not notable_positions:
            notable_positions = [self.node_positions[n] for n in allocated
                                  if n in self.node_positions]
        if notable_positions:
            cx = sum(p[0] for p in notable_positions) / len(notable_positions)
            cy = sum(p[1] for p in notable_positions) / len(notable_positions)
            centroid: tuple[float, float] | None = (cx, cy)
        else:
            centroid = None

        # BFS outward to collect candidate nodes (gather 5× needed for sorting)
        visited: set[int] = set(allocated)
        queue: deque[int] = deque()

        for nid in allocated:
            for neighbor in self.graph.get(nid, set()):
                if neighbor not in visited and neighbor not in forbidden:
                    info = self.node_info.get(neighbor)
                    if not info:
                        continue
                    if info.get("ascendancy") or info.get("is_mastery"):
                        continue
                    if info.get("class_start_index") is not None:
                        continue
                    visited.add(neighbor)
                    queue.append(neighbor)

        candidates: list[int] = []
        while queue and len(candidates) < needed * 5:
            node = queue.popleft()
            # Never directly allocate a conflict node as padding —
            # they may still be traversed as intermediaries during A* pathing
            if conflict_nodes and node in conflict_nodes:
                continue
            candidates.append(node)
            for neighbor in self.graph.get(node, set()):
                if neighbor in visited or neighbor in forbidden:
                    continue
                info = self.node_info.get(neighbor)
                if not info:
                    continue
                if info.get("ascendancy") or info.get("is_mastery"):
                    continue
                if info.get("class_start_index") is not None:
                    continue
                visited.add(neighbor)
                queue.append(neighbor)

        def pad_score(nid: int) -> float:
            """Lower = higher priority."""
            info = self.node_info.get(nid, {})
            # Centroid distance: prefer nodes close to the build's active area
            pos = self.node_positions.get(nid)
            if pos and centroid:
                dist_score = math.hypot(pos[0] - centroid[0], pos[1] - centroid[1]) / 2000.0
            else:
                dist_score = 1.0
            # Stat relevance: reuse traversal cost (lower = better)
            stat_score = self._node_traversal_cost(
                nid, damage_type, weapon_type, attack_style
            )
            # Type bonus — only reward notables that have relevant stats
            # (stat_score already reflects relevance; a generic type bonus
            #  would pull in unrelated notables like Conduit or Solipsism)
            if info.get("is_notable") and stat_score < 0.9:
                type_bonus = -1.0  # modest bonus for relevant notables only
            elif info.get("is_jewel_socket"):
                type_bonus = -1.5
            else:
                type_bonus = 0.0
            return dist_score + stat_score + type_bonus

        candidates.sort(key=pad_score)

        added = 0
        for candidate in candidates:
            if added >= needed:
                break
            if candidate in allocated:
                continue
            # Fast path: directly adjacent to tree
            if self.graph.get(candidate, set()) & allocated:
                allocated.add(candidate)
                added += 1
            else:
                # Need to path to it — only add if it fits within budget
                path = self._astar_single(
                    allocated, candidate, forbidden,
                    damage_type, weapon_type, attack_style,
                )
                if path is None:
                    continue
                new_nodes = [n for n in path if n not in allocated]
                if added + len(new_nodes) > needed:
                    continue
                for n in new_nodes:
                    allocated.add(n)
                    added += 1

        final = self._count_main_nodes(allocated)
        logger.info(f"Padding complete: {final}/{target_points} main-tree points")

    def _astar_to_nearest(self, sources: set[int],
                           targets: set[int],
                           forbidden: set[int],
                           damage_type: str = "",
                           weapon_type: str = "",
                           attack_style: str = "") -> list[int] | None:
        """
        Multi-source weighted A*: find the lowest-cost path from any source
        to any target, using Euclidean distance as heuristic and stat-based
        node costs as edge weights. Conflict nodes are in `forbidden` and
        are never traversed.
        """
        if not targets:
            return None

        # Precompute target positions for heuristic
        target_positions = [
            self.node_positions[t] for t in targets if t in self.node_positions
        ]

        def heuristic(nid: int) -> float:
            pos = self.node_positions.get(nid)
            if pos is None or not target_positions:
                return 0.0
            return min(
                math.hypot(pos[0] - tp[0], pos[1] - tp[1])
                for tp in target_positions
            ) / _ASTAR_HEURISTIC_SCALE

        g_score: dict[int, float] = {}
        parent: dict[int, int | None] = {}
        open_heap: list[tuple[float, float, int]] = []  # (f, g, node_id)

        for s in sources:
            g_score[s] = 0.0
            parent[s] = None
            heapq.heappush(open_heap, (heuristic(s), 0.0, s))

        while open_heap:
            f, g, current = heapq.heappop(open_heap)

            if g > g_score.get(current, float("inf")) + 1e-9:
                continue  # stale heap entry

            if current in targets:
                path: list[int] = []
                node: int | None = current
                while node is not None:
                    path.append(node)
                    node = parent.get(node)
                path.reverse()
                return path

            for neighbor in self.graph.get(current, set()):
                if neighbor in forbidden:
                    continue
                info = self.node_info.get(neighbor)
                if info and info.get("ascendancy"):
                    continue
                if info and info.get("is_mastery"):
                    continue
                if info and info.get("class_start_index") is not None:
                    if neighbor not in sources and neighbor not in targets:
                        continue

                edge_cost = self._node_traversal_cost(
                    neighbor, damage_type, weapon_type, attack_style
                )
                new_g = g + edge_cost

                if new_g < g_score.get(neighbor, float("inf")):
                    g_score[neighbor] = new_g
                    parent[neighbor] = current
                    heapq.heappush(open_heap,
                                   (new_g + heuristic(neighbor), new_g, neighbor))

        return None

    def _astar_single(self, sources: set[int],
                       target: int,
                       forbidden: set[int],
                       damage_type: str = "",
                       weapon_type: str = "",
                       attack_style: str = "") -> list[int] | None:
        """
        A* from the source set to a single target.
        Returns only the new nodes (those not already in sources), or None.
        """
        path = self._astar_to_nearest(
            sources, {target}, forbidden,
            damage_type, weapon_type, attack_style,
        )
        if path is None:
            return None
        return [n for n in path if n not in sources]


# ── Module-level singleton ────────────────────────────────────────────────────
_tree: PassiveTree | None = None


def get_tree() -> PassiveTree:
    """Get or create the singleton PassiveTree."""
    global _tree
    if _tree is None:
        _tree = PassiveTree()
        _tree.load()
    return _tree


def compute_allocated_nodes(class_name: str,
                            notable_names: list[str],
                            ascendancy_name: str = "",
                            damage_type: str = "",
                            weapon_type: str = "",
                            attack_style: str = "") -> tuple[list[int], list[str], list[str], list[int]]:
    """
    High-level API: given a class, ascendancy, and target notables, return
    (full_node_id_list, matched_names, unmatched_names, jewel_socket_ids).
    Ascendancy notables are auto-selected based on build parameters.
    """
    tree = get_tree()
    return tree.compute_build_tree(class_name, notable_names, ascendancy_name,
                                   damage_type, weapon_type, attack_style)
