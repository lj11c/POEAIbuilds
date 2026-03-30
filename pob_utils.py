"""
POB XML generation and import code encoding/decoding.

POB import codes are: zlib.compress(xml_bytes) → base64 (with + → -, / → _)
"""

import zlib
import base64
import xml.etree.ElementTree as ET
from xml.dom import minidom

from data_parser import find_gem
from tree_pathfinder import compute_allocated_nodes

# POE 1 class and ascendancy metadata
CLASS_INFO: dict[str, dict] = {
    "Scion":    {"classId": 0, "ascendancies": {"Ascendant": 1}},
    "Marauder": {"classId": 1, "ascendancies": {"Juggernaut": 1, "Berserker": 2, "Chieftain": 3}},
    "Ranger":   {"classId": 2, "ascendancies": {"Deadeye": 1, "Raider": 2, "Pathfinder": 3}},
    "Witch":    {"classId": 3, "ascendancies": {"Occultist": 1, "Necromancer": 2, "Elementalist": 3}},
    "Duelist":  {"classId": 4, "ascendancies": {"Slayer": 1, "Gladiator": 2, "Champion": 3}},
    "Templar":  {"classId": 5, "ascendancies": {"Inquisitor": 1, "Hierophant": 2, "Guardian": 3}},
    "Shadow":   {"classId": 6, "ascendancies": {"Assassin": 1, "Saboteur": 2, "Trickster": 3}},
}

# Reverse lookup: ascendancy name → class name
ASCENDANCY_TO_CLASS: dict[str, str] = {}
for _cls, _meta in CLASS_INFO.items():
    for _asc in _meta["ascendancies"]:
        ASCENDANCY_TO_CLASS[_asc] = _cls


def validate_class_ascendancy(class_name: str, ascendancy_name: str) -> tuple[str, str]:
    """
    Validate that the ascendancy belongs to the class.
    If mismatched, fix whichever is wrong:
      - If the ascendancy is valid but for a different class, fix the class.
      - If the class is valid but the ascendancy isn't one of its options, pick the first.
    Returns (corrected_class, corrected_ascendancy).
    """
    class_meta = CLASS_INFO.get(class_name)

    if class_meta and ascendancy_name in class_meta["ascendancies"]:
        return class_name, ascendancy_name  # all good

    # Ascendancy exists but belongs to a different class — fix the class
    if ascendancy_name in ASCENDANCY_TO_CLASS:
        correct_class = ASCENDANCY_TO_CLASS[ascendancy_name]
        import logging
        logging.getLogger(__name__).warning(
            f"Ascendancy '{ascendancy_name}' belongs to {correct_class}, not {class_name} — fixing class"
        )
        return correct_class, ascendancy_name

    # Class is valid but ascendancy is garbage — pick the first ascendancy for this class
    if class_meta:
        first_asc = next(iter(class_meta["ascendancies"]))
        import logging
        logging.getLogger(__name__).warning(
            f"Unknown ascendancy '{ascendancy_name}' for {class_name} — defaulting to {first_asc}"
        )
        return class_name, first_asc

    # Both are bad — default to Scion/Ascendant
    import logging
    logging.getLogger(__name__).warning(
        f"Unknown class '{class_name}' and ascendancy '{ascendancy_name}' — defaulting to Scion/Ascendant"
    )
    return "Scion", "Ascendant"


def _default_jewel_mods(build_data: dict) -> list[str]:
    """
    Generate sensible default jewel mods based on build parameters
    when Claude doesn't provide specific jewel definitions.
    """
    damage_type = (build_data.get("damage_type") or "").lower()
    weapon_type = (build_data.get("weapon_type") or "").lower()
    mods = ["7% increased maximum Life"]

    if "fire" in damage_type:
        mods.append("+15% to Fire Damage over Time Multiplier" if "dot" in damage_type or "ignite" in damage_type
                     else "15% increased Fire Damage")
    elif "cold" in damage_type:
        mods.append("15% increased Cold Damage")
    elif "lightning" in damage_type:
        mods.append("15% increased Lightning Damage")
    elif "chaos" in damage_type:
        mods.append("15% increased Chaos Damage")
    elif "physical" in damage_type:
        mods.append("15% increased Physical Damage")
    else:
        mods.append("10% increased Damage")

    if "spell" in (build_data.get("attack_style") or "").lower():
        mods.append("12% increased Spell Damage")
        mods.append("+12% to Global Critical Strike Multiplier")
    elif "attack" in (build_data.get("attack_style") or "").lower() or weapon_type:
        mods.append("12% increased Attack Speed")
        mods.append("+12% to Global Critical Strike Multiplier")
    else:
        mods.append("+12% to Global Critical Strike Multiplier")
        mods.append("+15% to all Elemental Resistances")

    return mods


def _indent_xml(elem: ET.Element, level: int = 0):
    """Add pretty-print indentation to an ElementTree in place."""
    indent = "\n" + "\t" * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = indent + "\t"
        if not elem.tail or not elem.tail.strip():
            elem.tail = indent
        for child in elem:
            _indent_xml(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = indent
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = indent
    if not level:
        elem.tail = "\n"


def build_pob_xml(build_data: dict) -> str:
    """
    Generate a POB-compatible XML string from a structured build dict.

    Expected build_data keys:
      class_name, ascendancy_name, level (int),
      skill_setups: list of {slot, is_main, gems: [{name, level, quality, is_support}]}
      passive_notables: list of notable/keystone names (strings)
      notes: str (full build guide text)
    """
    class_name = build_data.get("class_name", "Scion")
    ascendancy_name = build_data.get("ascendancy_name", "")
    level = str(build_data.get("level", 90))

    # Validate class/ascendancy match — fix if Claude got it wrong
    class_name, ascendancy_name = validate_class_ascendancy(class_name, ascendancy_name)
    build_data["class_name"] = class_name
    build_data["ascendancy_name"] = ascendancy_name

    class_meta = CLASS_INFO.get(class_name, CLASS_INFO["Scion"])
    class_id = str(class_meta["classId"])
    ascend_id = str(class_meta["ascendancies"].get(ascendancy_name, 0))

    root = ET.Element("PathOfBuilding")

    # ── Build element ──────────────────────────────────────────────────────────
    build_el = ET.SubElement(root, "Build",
        level=level,
        targetVersion="3_0",
        pantheonMajorGod="None",
        bandit="None",
        className=class_name,
        ascendClassName=ascendancy_name,
        mainSocketGroup="1",
        viewMode="MAIN",
        pantheonMinorGod="None",
    )
    # Placeholder stat so POB knows it's a valid build
    ET.SubElement(build_el, "PlayerStat", stat="Life", value="0")

    # ── Import / Calcs ────────────────────────────────────────────────────────
    ET.SubElement(root, "Import")
    ET.SubElement(root, "Calcs")

    # ── Skills ───────────────────────────────────────────────────────────────
    skills_el = ET.SubElement(root, "Skills",
        sortGemsByDPSField="CombinedDPS",
        sortGemsByDPS="true",
        defaultGemQuality="nil",
        defaultGemLevel="nil",
        showSupportGemTypes="ALL",
        showAltQualityGems="false",
    )

    skill_setups = build_data.get("skill_setups", [])
    for idx, setup in enumerate(skill_setups, start=1):
        slot = setup.get("slot", "Body Armour")
        skill_el = ET.SubElement(skills_el, "Skill",
            mainActiveSkillCalcs="1",
            enabled="true",
            slot=slot,
            mainActiveSkill="1",
        )
        for gem in setup.get("gems", []):
            gem_name = gem.get("name", "")
            gem_level = str(gem.get("level", 20))
            gem_quality = str(gem.get("quality", 0))

            gem_info = find_gem(gem_name)
            gem_id = gem_info["gemId"] if gem_info else ""
            skill_id = gem_info["skillId"] if gem_info else ""

            ET.SubElement(skill_el, "Gem",
                enableGlobal2="true",
                level=gem_level,
                gemId=gem_id,
                skillId=skill_id,
                enableGlobal1="true",
                qualityId="Default",
                quality=gem_quality,
                enabled="true",
                nameSpec=gem_name,
            )

    # ── Passive Tree ─────────────────────────────────────────────────────────
    # Use pathfinder to compute full connected tree from notable names
    # Pass weapon_type and attack_style so the pathfinder avoids conflicting nodes
    passive_names = build_data.get("passive_notables", [])
    damage_type = build_data.get("damage_type", "")
    weapon_type = build_data.get("weapon_type", "")
    attack_style = build_data.get("attack_style", "")
    node_ids, matched_nodes, unmatched_nodes, jewel_socket_ids, mastery_effects = compute_allocated_nodes(
        class_name, passive_names, ascendancy_name, damage_type, weapon_type, attack_style
    )
    nodes_str = ",".join(str(nid) for nid in node_ids) if node_ids else ""

    # Store match info for the API response
    build_data["_matched_nodes"] = matched_nodes
    build_data["_unmatched_nodes"] = unmatched_nodes
    build_data["_total_nodes_allocated"] = len(node_ids)
    build_data["_jewel_sockets_used"] = len(jewel_socket_ids)
    build_data["_mastery_effects_count"] = len(mastery_effects)

    # Build masteryEffects attribute: "{nodeId,effectId},{nodeId,effectId},..."
    mastery_str = ",".join(
        f"{{{nid},{eff_id}}}" for nid, eff_id in mastery_effects.items()
    ) if mastery_effects else ""

    tree_el = ET.SubElement(root, "Tree", activeSpec="1")
    spec_attrs = dict(
        ascendClassId=ascend_id,
        nodes=nodes_str,
        treeVersion="3_28",
        classId=class_id,
    )
    if mastery_str:
        spec_attrs["masteryEffects"] = mastery_str
    spec_el = ET.SubElement(tree_el, "Spec", **spec_attrs)

    # ── Jewels ────────────────────────────────────────────────────────────────
    # Generate custom rare jewels for each allocated jewel socket
    jewel_defs = build_data.get("jewels", [])
    sockets_el = ET.SubElement(spec_el, "Sockets")

    # ── Items ─────────────────────────────────────────────────────────────────
    items_el = ET.SubElement(root, "Items",
        activeItemSet="1",
        useSecondWeaponSet="false",
    )
    item_set_el = ET.SubElement(items_el, "ItemSet", id="1")

    # Create jewel items and socket them
    for idx, socket_node_id in enumerate(jewel_socket_ids):
        item_id = idx + 1

        # Use AI-generated jewel mods if available, otherwise use defaults
        if idx < len(jewel_defs):
            jewel_def = jewel_defs[idx]
        else:
            jewel_def = {}

        jewel_name = jewel_def.get("name", f"Build Jewel {item_id}")
        jewel_base = jewel_def.get("base", "Cobalt Jewel")
        jewel_mods = jewel_def.get("mods", _default_jewel_mods(build_data))

        # Build the item text in POB format
        item_lines = [
            f"Rarity: RARE",
            jewel_name,
            jewel_base,
            f"Item Level: 84",
            f"Implicits: 0",
        ]
        for mod in jewel_mods:
            item_lines.append(mod)

        item_el = ET.SubElement(items_el, "Item", id=str(item_id))
        item_el.text = "\n" + "\n".join(item_lines) + "\n"

        # Link socket to item
        ET.SubElement(sockets_el, "Socket",
            nodeId=str(socket_node_id),
            itemId=str(item_id),
        )

    # ── Notes (build guide) ───────────────────────────────────────────────────
    notes_el = ET.SubElement(root, "Notes")
    notes_el.text = build_data.get("notes", "")

    # ── Tree view settings ────────────────────────────────────────────────────
    ET.SubElement(root, "TreeView",
        searchStr="",
        zoomY="0",
        showHeatMap="false",
        zoomLevel="3",
        showStatDifferences="true",
        zoomX="0",
    )

    # ── Serialize ─────────────────────────────────────────────────────────────
    _indent_xml(root)
    xml_bytes = ET.tostring(root, encoding="unicode", xml_declaration=False)

    # POB's Lua XML parser errors on self-closing tags for elements it expects
    # to have text content. Force open/close form for known problem elements.
    for tag in ("URL", "Notes", "EditedNodes"):
        xml_bytes = xml_bytes.replace(f"<{tag} />", f"<{tag}></{tag}>")
        xml_bytes = xml_bytes.replace(f"<{tag}/>", f"<{tag}></{tag}>")

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_bytes


def xml_to_import_code(xml_str: str) -> str:
    """
    Encode a POB XML string into a POB import code.
    Format: base64(zlib_compress(utf8_bytes)) with + → - and / → _
    """
    raw = xml_str.encode("utf-8")
    compressed = zlib.compress(raw)
    code = base64.b64encode(compressed).decode("ascii")
    return code.replace("+", "-").replace("/", "_")


def import_code_to_xml(code: str) -> str:
    """Decode a POB import code back to an XML string."""
    normalized = code.replace("-", "+").replace("_", "/")
    compressed = base64.b64decode(normalized)
    return zlib.decompress(compressed).decode("utf-8")


def generate_import_code(build_data: dict) -> tuple[str, str]:
    """
    Generate both the POB XML and its import code from build_data.
    Returns (xml_str, import_code).
    """
    xml_str = build_pob_xml(build_data)
    import_code = xml_to_import_code(xml_str)
    return xml_str, import_code
