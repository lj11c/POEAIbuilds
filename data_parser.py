"""
Parsers for POB data files: tree.lua and Gems.lua.
Loaded once at startup, results cached in memory.
"""

import re
import os
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

POB_DIR = os.path.join(os.path.dirname(__file__), "POB", "PathOfBuilding-dev")
TREE_LUA_PATH = os.path.join(POB_DIR, "src", "TreeData", "3_28", "tree.lua")
GEMS_LUA_PATH = os.path.join(POB_DIR, "src", "Data", "Gems.lua")

# Maps lowercase node name → list of node dicts
_tree_nodes: dict[str, list[dict]] = {}
# Maps lowercase gem name → {gemId, skillId, name}
_gem_lookup: dict[str, dict] = {}
_data_loaded = False


def _parse_tree_nodes(filepath: str) -> dict[str, list[dict]]:
    """
    Parse tree.lua and return a dict mapping lowercase name → list of node info dicts.
    Each dict: {id, name, is_notable, is_keystone, ascendancy}
    """
    logger.info("Parsing tree.lua...")
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    # The top-level ["nodes"] section is the last occurrence
    idx = content.rfind('["nodes"]=')
    if idx == -1:
        logger.error("Could not find ['nodes'] section in tree.lua")
        return {}

    # Find the opening brace of the nodes table
    brace_start = content.index("{", idx)
    pos = brace_start + 1
    content_len = len(content)

    nodes_by_name: dict[str, list[dict]] = {}
    node_id_re = re.compile(r"\[(\d+)\]=\s*\{")

    while pos < content_len:
        # Skip whitespace
        while pos < content_len and content[pos] in " \t\n\r":
            pos += 1
        if pos >= content_len:
            break

        # End of nodes table
        if content[pos] == "}":
            break

        # Skip "root" special node
        if content[pos : pos + 7] == '["root"':
            while pos < content_len and content[pos] != "{":
                pos += 1
            depth = 0
            while pos < content_len:
                if content[pos] == "{":
                    depth += 1
                elif content[pos] == "}":
                    depth -= 1
                    if depth == 0:
                        pos += 1
                        break
                pos += 1
            continue

        # Match numeric node ID
        m = node_id_re.match(content, pos)
        if not m:
            pos += 1
            continue

        node_id = int(m.group(1))
        block_start = content.index("{", pos)
        pos = block_start

        # Find matching closing brace using depth counting
        depth = 0
        block_end = pos
        while block_end < content_len:
            c = content[block_end]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            block_end += 1

        block = content[block_start : block_end + 1]

        name_m = re.search(r'"name"\]\s*=\s*"([^"]+)"', block)
        if name_m:
            name = name_m.group(1)
            is_notable = '"isNotable"]= true' in block
            is_keystone = '"isKeystone"]= true' in block
            is_mastery = '"isMastery"]= true' in block
            asc_m = re.search(r'"ascendancyName"\]\s*=\s*"([^"]+)"', block)
            ascendancy = asc_m.group(1) if asc_m else None

            node_info = {
                "id": node_id,
                "name": name,
                "is_notable": is_notable,
                "is_keystone": is_keystone,
                "is_mastery": is_mastery,
                "ascendancy": ascendancy,
            }
            key = name.lower()
            nodes_by_name.setdefault(key, []).append(node_info)

        pos = block_end + 1

    logger.info(f"Parsed {sum(len(v) for v in nodes_by_name.values())} nodes ({len(nodes_by_name)} unique names)")
    return nodes_by_name


def _parse_gems_lua(filepath: str) -> dict[str, dict]:
    """
    Parse Gems.lua and return a dict mapping lowercase gem name → {gemId, skillId, name}.
    Skips Vaal variants and alt-quality variants when a base version exists.
    """
    logger.info("Parsing Gems.lua...")
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    # Each gem entry: ["Metadata/Items/Gems/SkillGemXxx"] = { name = "...", variantId = "...", ... }
    # We only need the top-level block (before the nested tags = {})
    pattern = re.compile(
        r'\["(Metadata/Items/Gems/[^"]+)"\]\s*=\s*\{([^{]*)',
        re.DOTALL,
    )

    gems: dict[str, dict] = {}
    for m in pattern.finditer(content):
        gem_path = m.group(1)
        block = m.group(2)

        name_m = re.search(r'name\s*=\s*"([^"]+)"', block)
        variant_m = re.search(r'variantId\s*=\s*"([^"]+)"', block)
        if not name_m or not variant_m:
            continue

        name = name_m.group(1)
        variant_id = variant_m.group(1)
        key = name.lower()

        # Prefer non-Vaal, non-alt versions (no "Vaal" in name, first seen wins)
        if key not in gems:
            gems[key] = {"gemId": gem_path, "skillId": variant_id, "name": name}

    logger.info(f"Parsed {len(gems)} gems")
    return gems


def load_data():
    """Load and cache all POB data. Call once at startup."""
    global _tree_nodes, _gem_lookup, _data_loaded
    if _data_loaded:
        return

    if os.path.exists(TREE_LUA_PATH):
        _tree_nodes = _parse_tree_nodes(TREE_LUA_PATH)
    else:
        logger.warning(f"tree.lua not found at {TREE_LUA_PATH}")

    if os.path.exists(GEMS_LUA_PATH):
        _gem_lookup = _parse_gems_lua(GEMS_LUA_PATH)
    else:
        logger.warning(f"Gems.lua not found at {GEMS_LUA_PATH}")

    _data_loaded = True


def find_node_ids(names: list[str]) -> tuple[list[int], list[str], list[str]]:
    """
    Given a list of passive node names, return:
      (node_ids, matched_names, unmatched_names)

    Tries exact match first, then falls back to a 'starts with' fuzzy match.
    Excludes ascendancy nodes.
    """
    node_ids: list[int] = []
    matched: list[str] = []
    unmatched: list[str] = []

    for name in names:
        key = name.lower().strip()

        # 1. Exact match
        candidates = _tree_nodes.get(key, [])

        # 2. Fuzzy fallback — find any node whose name starts with the search term
        if not candidates:
            for node_key, nodes in _tree_nodes.items():
                if node_key.startswith(key) or key.startswith(node_key):
                    candidates = nodes
                    break

        # 3. Fuzzy fallback — find any node whose name contains the search term
        if not candidates:
            for node_key, nodes in _tree_nodes.items():
                if key in node_key:
                    candidates = nodes
                    break

        # Prefer main-tree (non-ascendancy) nodes
        main_tree = [n for n in candidates if not n["ascendancy"]]
        chosen = main_tree[0] if main_tree else (candidates[0] if candidates else None)

        if chosen:
            node_ids.append(chosen["id"])
            matched.append(f"{name} → {chosen['name']} (#{chosen['id']})")
        else:
            logger.warning(f"Passive node not found: '{name}'")
            unmatched.append(name)

    logger.info(f"Passive nodes: {len(node_ids)} matched, {len(unmatched)} unmatched")
    if unmatched:
        logger.warning(f"Unmatched passives: {unmatched}")

    return node_ids, matched, unmatched


def find_gem(name: str) -> dict | None:
    """Return gem info dict {gemId, skillId, name} for a gem name, or None."""
    return _gem_lookup.get(name.lower().strip())
