"""
Passive tree pathfinding engine.

Parses the tree.lua graph and computes the shortest connected tree
from a class start node through a set of target notable/keystone nodes.

Uses a greedy Steiner-tree approximation:
  1. Start from the class start node
  2. Repeatedly find the closest unvisited target (BFS)
  3. Add all nodes along that shortest path
  4. Repeat until all targets are reached
"""

import re
import os
import logging
from collections import deque

logger = logging.getLogger(__name__)

POB_DIR = os.path.join(os.path.dirname(__file__), "POB", "PathOfBuilding-dev")
TREE_LUA_PATH = os.path.join(POB_DIR, "src", "TreeData", "3_28", "tree.lua")


class PassiveTree:
    """In-memory passive tree graph with pathfinding."""

    def __init__(self):
        # node_id (int) → set of neighbor node_ids (int)
        self.graph: dict[int, set[int]] = {}
        # node_id → {"name", "is_notable", "is_keystone", "is_mastery", "ascendancy", "class_start_index"}
        self.node_info: dict[int, dict] = {}
        # lowercase name → list of node_ids
        self.name_to_ids: dict[str, list[int]] = {}
        # classStartIndex → node_id
        self.class_starts: dict[int, int] = {}
        # set of jewel socket node IDs
        self.jewel_sockets: set[int] = set()
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

        # Find the main ["nodes"] section (the last top-level one)
        idx = content.rfind('["nodes"]=')
        if idx == -1:
            raise ValueError("Could not find nodes section in tree.lua")

        brace_start = content.index("{", idx)
        self._parse_nodes(content, brace_start)

        # Build undirected graph from out/in edges
        logger.info(f"Tree loaded: {len(self.node_info)} nodes, "
                     f"{len(self.class_starts)} class starts, "
                     f"{len(self.name_to_ids)} unique names")

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
        WEAPON_CONFLICTS = {
            "sword":  ["with axes", "with maces", "with claws", "with daggers",
                       "with bows", "with wands", "with staves"],
            "axe":    ["with swords", "with maces", "with claws", "with daggers",
                       "with bows", "with wands", "with staves"],
            "mace":   ["with swords", "with axes", "with claws", "with daggers",
                       "with bows", "with wands", "with staves"],
            "claw":   ["with swords", "with axes", "with maces", "with daggers",
                       "with bows", "with wands", "with staves"],
            "dagger": ["with swords", "with axes", "with maces", "with claws",
                       "with bows", "with wands", "with staves"],
            "bow":    ["with swords", "with axes", "with maces", "with claws",
                       "with daggers", "with wands", "with staves",
                       "while holding a shield", "while dual wielding"],
            "wand":   ["with swords", "with axes", "with maces", "with claws",
                       "with daggers", "with bows", "with staves"],
            "staff":  ["with swords", "with axes", "with maces", "with claws",
                       "with daggers", "with bows", "with wands",
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

        # Scan all non-notable, non-keystone nodes for conflicting stats
        conflicting: set[int] = set()
        for nid, info in self.node_info.items():
            if info.get("is_notable") or info.get("is_keystone"):
                continue
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

        # Build set of forbidden nodes: root node + OTHER class start nodes
        # In PoE you cannot path through another class's start node
        forbidden: set[int] = {-1}  # root pseudo-node
        for cidx, nid in self.class_starts.items():
            if cidx != class_idx:
                forbidden.add(nid)

        # Add small passives with conflicting stats to forbidden set
        conflict_nodes = self.compute_conflict_nodes(weapon_type, attack_style)
        # Don't forbid any target nodes — the user explicitly asked for them
        conflict_nodes -= set(target_ids)
        forbidden.update(conflict_nodes)

        logger.info(f"Pathfinding: {class_name} (start #{start_id}) -> "
                     f"{len(target_ids)} targets ({len(unmatched)} unmatched), "
                     f"{len(forbidden)} forbidden nodes")

        # Greedy Steiner tree: keep adding closest target
        allocated: set[int] = {start_id}
        remaining_targets = set(target_ids)

        while remaining_targets:
            best_path: list[int] | None = None
            best_target: int | None = None

            # BFS from ALL currently allocated nodes simultaneously
            # to find the closest remaining target
            best_path = self._bfs_to_nearest(allocated, remaining_targets, forbidden)

            if best_path is None:
                # Can't reach any remaining targets
                unreachable = []
                for tid in remaining_targets:
                    info = self.node_info.get(tid, {})
                    unreachable.append(info.get("name", str(tid)))
                logger.warning(f"Cannot reach targets: {unreachable}")
                break

            # Add all nodes on the path to the allocated set
            best_target = best_path[-1]
            allocated.update(best_path)
            remaining_targets.discard(best_target)

        # Find jewel sockets near the allocated path and add them
        # (do this before padding so they count toward the 120 target)
        nearby_sockets = self.find_allocated_jewel_sockets(allocated)
        for sid in nearby_sockets:
            if sid not in allocated:
                allocated.add(sid)

        # ── Pad main tree to exactly 120 points ───────────────────────────
        self._pad_to_target(allocated, forbidden, target_points=120)

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
                        target_points: int = 120):
        """
        Expand the allocated tree outward until we reach exactly
        target_points main-tree nodes. Uses BFS from the frontier
        of the current tree, preferring notables and jewel sockets
        over plain small passives.
        """
        current = self._count_main_nodes(allocated)
        if current >= target_points:
            logger.info(f"Tree already has {current}/{target_points} main-tree points — no padding needed")
            return

        needed = target_points - current
        logger.info(f"Padding tree: {current} → {target_points} ({needed} nodes to add)")

        # BFS outward from the current tree to find candidate nodes
        # Score them: notables and jewel sockets are preferred
        visited: set[int] = set(allocated)
        queue: deque[int] = deque()

        # Seed with all frontier neighbors
        for nid in allocated:
            for neighbor in self.graph.get(nid, set()):
                if neighbor not in visited and neighbor not in forbidden:
                    info = self.node_info.get(neighbor)
                    if not info:
                        continue
                    if info.get("ascendancy") or info.get("is_mastery"):
                        continue
                    # Skip other class start nodes
                    if info.get("class_start_index") is not None and neighbor not in allocated:
                        continue
                    visited.add(neighbor)
                    queue.append(neighbor)

        # Collect candidates in BFS order (closest first)
        candidates: list[int] = []
        while queue and len(candidates) < needed * 3:  # gather extras for sorting
            current_node = queue.popleft()
            candidates.append(current_node)

            for neighbor in self.graph.get(current_node, set()):
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

        # Sort candidates: notables first, then jewel sockets, then small passives
        # But we must maintain connectivity — add them one at a time via BFS path
        added = 0
        for candidate in candidates:
            if added >= needed:
                break

            # Make sure this node connects to the current allocated set
            info = self.node_info.get(candidate, {})

            # Check if it's directly adjacent to allocated
            neighbors = self.graph.get(candidate, set())
            if not (neighbors & allocated):
                # Need to path to it — find shortest path
                path = self._bfs_to_nearest_single(allocated, candidate, forbidden)
                if path is None:
                    continue
                # Only add if the whole path fits within budget
                new_in_path = [pn for pn in path if pn not in allocated]
                if added + len(new_in_path) > needed:
                    continue  # skip — would overshoot
                for pn in new_in_path:
                    allocated.add(pn)
                    added += 1
            else:
                allocated.add(candidate)
                added += 1

        final = self._count_main_nodes(allocated)
        logger.info(f"Padding complete: {final}/{target_points} main-tree points")

    def _bfs_to_nearest_single(self, sources: set[int], target: int,
                                forbidden: set[int]) -> list[int] | None:
        """BFS from sources to a single target, avoiding forbidden and ascendancy nodes."""
        visited: dict[int, int | None] = {}
        queue: deque[int] = deque()

        for s in sources:
            visited[s] = None
            queue.append(s)

        while queue:
            current = queue.popleft()
            if current == target:
                path = []
                node: int | None = current
                while node is not None:
                    path.append(node)
                    node = visited[node]
                path.reverse()
                return [n for n in path if n not in sources]  # only new nodes

            for neighbor in self.graph.get(current, set()):
                if neighbor in visited or neighbor in forbidden:
                    continue
                info = self.node_info.get(neighbor)
                if info and info.get("ascendancy"):
                    continue
                if info and info.get("is_mastery"):
                    continue
                if info and info.get("class_start_index") is not None:
                    if neighbor not in sources and neighbor != target:
                        continue
                visited[neighbor] = current
                queue.append(neighbor)

        return None

    def _bfs_to_nearest(self, sources: set[int],
                        targets: set[int],
                        forbidden: set[int] | None = None) -> list[int] | None:
        """
        Multi-source BFS: find shortest path from any node in `sources`
        to any node in `targets`, avoiding `forbidden` nodes.
        Returns the path, or None.
        """
        forbidden = forbidden or set()
        visited: dict[int, int | None] = {}  # node -> parent
        queue: deque[int] = deque()

        for s in sources:
            visited[s] = None
            queue.append(s)

        while queue:
            current = queue.popleft()

            if current in targets:
                # Reconstruct path from source to this target
                path = []
                node = current
                while node is not None:
                    path.append(node)
                    node = visited[node]
                path.reverse()
                return path

            for neighbor in self.graph.get(current, set()):
                if neighbor in visited or neighbor in forbidden:
                    continue
                info = self.node_info.get(neighbor)
                if info and info.get("ascendancy"):
                    continue
                if info and info.get("is_mastery"):
                    continue
                # Skip other class start nodes (can't path through them)
                if info and info.get("class_start_index") is not None:
                    if neighbor not in sources and neighbor not in targets:
                        continue
                visited[neighbor] = current
                queue.append(neighbor)

        return None


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
