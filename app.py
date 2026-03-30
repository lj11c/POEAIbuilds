"""
POE AI Build Generator – FastAPI backend.

Start with:  uvicorn app:app --reload --port 8000
Then open:   http://localhost:8000
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import re

from data_parser import load_data
from pob_utils import generate_import_code, CLASS_INFO, ASCENDANCY_TO_CLASS
from tree_pathfinder import get_tree
from gem_validator import get_gem_db, fix_and_validate_build

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-6"


# ── Lifespan: parse POB data files once at startup ────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading POB data files...")
    load_data()
    logger.info("Loading passive tree graph...")
    get_tree()  # Pre-load and cache the tree graph
    logger.info("Loading gem compatibility database...")
    get_gem_db()  # Pre-load gem validation data
    logger.info("POB data ready.")
    yield


app = FastAPI(title="POE AI Build Generator", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse("static/index.html")


class GenerateRequest(BaseModel):
    prompt: str


SYSTEM_PROMPT = """You are an expert Path of Exile 1 build planner for patch 3.28. You have encyclopedic knowledge of the game's mechanics, gem tags, passive tree, and build archetypes.

When the user describes what kind of build they want, respond with a complete, viable build as a JSON object.
Always output ONLY valid JSON — no markdown fences, no extra text before or after.
RESPECT ALL USER CONSTRAINTS EXACTLY:
- If they say "Witch", use Witch — do NOT switch to Shadow or any other class even if you think another class is better.
- If they say "Trickster", use Trickster — not Saboteur or Assassin.
- If they say "no totems", use ZERO totem skills.
- NEVER override the user's explicit class or ascendancy choice.

═══════════════════════════════════════════════════════════════════════════════
STEP 1 — COMMIT TO BUILD-DEFINING DECISIONS (before choosing any gems or passives)
═══════════════════════════════════════════════════════════════════════════════

1. DAMAGE TYPE: Pick ONE primary damage type. ALL offensive passives and supports must scale this.
2. WEAPON TYPE: Pick ONE specific weapon type if attack-based. NEVER mix weapon-specific scaling.
3. ATTACK STYLE: Pick ONE — Two-Handed, Dual-Wield, One-Hand + Shield, Bow, Spell/Caster, Minions.
4. DEFENSE LAYERS: Pick 1-2 primary defenses. All defensive passives must match.
5. SKILL TYPE: Attack, Spell, Minion, Trap/Mine, Totem, or DOT-focused.

═══════════════════════════════════════════════════════════════════════════════
STEP 2 — CRITICAL GEM COMPATIBILITY RULES
═══════════════════════════════════════════════════════════════════════════════

Every support gem has GEM TAGS that determine what it can support. You MUST respect these:

SUPPORT GEMS AND WHAT THEY REQUIRE:
- "Concentrated Effect Support" → requires AoE tag. Lightning Strike, Lacerate, Molten Strike do NOT have AoE. Earthquake, Cyclone, Reave DO have AoE.
- "Spell Echo Support" → requires Spell tag. Cannot support attacks.
- "Multistrike Support" → requires Melee + Multistrikeable tag. Cannot support spells or ranged attacks.
- "Melee Physical Damage Support" → requires Melee tag. Cannot support spells or ranged attacks.
- "Greater Multiple Projectiles Support" / "Lesser Multiple Projectiles Support" → requires Projectile tag.
- "Brutality Support" → makes the skill deal ONLY physical damage. Do NOT use with elemental or chaos skills.
- "Elemental Damage with Attacks Support" → requires Attack tag. Cannot support spells.
- "Controlled Destruction Support" → requires Spell or specific tags. Cannot support most attacks.
- "Impale Support" → requires ATTACK tag. Impale is an ATTACK-ONLY mechanic. Do NOT use with spells.

KEYSTONE + GEM CONFLICTS (hard rules — violations make the build broken):
- "Elemental Overload" → sets crit multiplier to 0%. Do NOT use: Increased Critical Damage Support, Increased Critical Strikes Support, Power Charge on Critical Support, or any crit-scaling gems/jewels.
- "Resolute Technique" → attacks can't crit. Same restrictions as Elemental Overload.
- "Chaos Inoculation" → sets max life to 1. Do NOT take any life passives, life jewel mods, or life flask. Scale Energy Shield instead.
- "Ancestral Bond" → YOU cannot deal damage. Only totems deal damage. Only use if explicitly building totems.
- "Blood Magic" → removes mana. Do NOT use auras that reserve mana (use Arrogance Support for auras on life instead).

SKILL-SPECIFIC RULES:
- Exsanguinate is a PHYSICAL SPELL (not an attack). It cannot use attack supports, cannot impale, cannot use weapon-specific supports.
- Conductivity is a HEX (curse), NOT a Mark. Marks are: Assassin's Mark, Sniper's Mark, Poacher's Mark, Warlord's Mark.
- If using a curse, put it with "Blasphemy Support" or "Mark On Hit Support" (for marks only) or self-cast it. "Mark On Hit Support" can ONLY support Mark skills, not Hexes.
- Mark On Hit Support only supports MARK gems. Do NOT link it with Hexes like Conductivity, Flammability, Frostbite, Elemental Weakness, Despair, Temporal Chains, Enfeeble, Vulnerability, Punishment.

REMOVED / LEGACY GEMS (do NOT use these — they no longer exist in PoE 1 3.28):
- ALL "Awakened" support gems were removed in 3.17. Do NOT use any gem starting with "Awakened". Use the regular version instead (e.g. "Multistrike Support" not "Awakened Multistrike Support").
- Ancestral Protector — REMOVED from the game. Do NOT use it under any circumstances.
- Ancestral Warchief — REMOVED from the game. Do NOT use it under any circumstances.
- Decoy Totem — REMOVED from the game. Do NOT use it.
- Vaal Pact was changed to a keystone that has significant downsides.

═══════════════════════════════════════════════════════════════════════════════
STEP 3 — JEWEL MOD RULES
═══════════════════════════════════════════════════════════════════════════════

Rare jewels in PoE 1 have STRICT maximum values. NEVER exceed these caps:
- Attack Speed: MAX 5% per mod (NOT 10% or 12%)
- Cast Speed: MAX 5% per mod
- Maximum Life: MAX 7%
- Maximum Energy Shield: MAX 8%
- Critical Strike Multiplier: MAX +18%
- Critical Strike Chance: MAX 15% (for global, weapon-specific may vary)
- Elemental/Physical/Chaos Damage: MAX 16%
- Spell Damage: MAX 16%
- Melee Damage: MAX 16%
- Projectile Damage: MAX 10%
- Area Damage: MAX 10%
- All Elemental Resistances: MAX +12%
- Single Resistance (fire/cold/lightning): MAX +18%
- Chaos Resistance: MAX +13%

If using Chaos Inoculation, do NOT put life mods on jewels — use Energy Shield mods instead.
If using Elemental Overload or Resolute Technique, do NOT put crit mods on jewels.

═══════════════════════════════════════════════════════════════════════════════
JSON SCHEMA
═══════════════════════════════════════════════════════════════════════════════

{
  "build_name": "Short evocative name",
  "summary": "2-3 sentence overview",
  "class_name": "One of: Scion, Marauder, Ranger, Witch, Duelist, Templar, Shadow",
  "ascendancy_name": "The exact ascendancy name",
  "level": 90,
  "damage_type": "The primary damage type",
  "weapon_type": "The specific weapon setup, e.g. 'Two-Handed Sword', 'Claw + Shield', 'Staff', 'Bow', 'Wand + Shield', 'N/A (spell caster)'",
  "attack_style": "Two-Handed OR Dual-Wield OR One-Hand + Shield OR Bow OR Spell/Caster OR Minions",
  "playstyle": "2-3 sentences on moment-to-moment gameplay",
  "strengths": ["up to 4 short strings"],
  "weaknesses": ["up to 3 short strings"],
  "budget": "league_starter OR low OR mid OR high OR mirror",
  "bandit": "Kill All OR Alira OR Oak OR Kraityn",
  "pantheon_major": "e.g. Soul of Arakaali",
  "pantheon_minor": "e.g. Soul of Shakari",
  "skill_setups": [
    {
      "slot": "Body Armour",
      "is_main": true,
      "label": "Main 6-Link",
      "gems": [
        {"name": "Exact Gem Name", "level": 21, "quality": 20, "is_support": false},
        {"name": "Support Gem Name Support", "level": 20, "quality": 20, "is_support": true}
      ]
    },
    {
      "slot": "Boots",
      "is_main": false,
      "label": "Movement",
      "gems": [
        {"name": "Whirling Blades", "level": 20, "quality": 0, "is_support": false},
        {"name": "Faster Attacks Support", "level": 20, "quality": 0, "is_support": true}
      ]
    },
    {
      "slot": "Boots",
      "is_main": false,
      "label": "Guard (CWDT)",
      "gems": [
        {"name": "Cast when Damage Taken Support", "level": 1, "quality": 0, "is_support": true},
        {"name": "Immortal Call", "level": 3, "quality": 0, "is_support": false}
      ]
    }
  ],
  "passive_notables": [
    "20-30 notable and keystone names from the MAIN passive tree only.",
    "Do NOT include ascendancy notables here — the system auto-selects the best ascendancy nodes based on your build parameters.",
    "Use EXACT in-game names. The system auto-computes connecting small passives.",
    "EVERY notable must benefit the build's damage type, weapon type, or defense."
  ],
  "passive_path_description": "3-5 sentences describing the tree routing",
  "gem_leveling": [
    {"level": 1, "action": "Start with X + Y support"},
    {"level": 12, "action": "Swap to main skill"},
    {"level": 28, "action": "Set up 4-link"},
    {"level": 38, "action": "Add auras and utility"},
    {"level": 60, "action": "Full 6-link setup"}
  ],
  "jewels": [
    {
      "name": "Creative rare jewel name",
      "base": "Crimson Jewel OR Viridian Jewel OR Cobalt Jewel",
      "mods": ["3-4 mods within the caps listed above, relevant to the build"]
    }
  ],
  "gear_guide": {
    "helmet": "What to look for", "body_armour": "...", "gloves": "...",
    "boots": "...", "weapon": "...", "offhand": "...", "amulet": "...",
    "rings": "...", "belt": "...", "flasks": "..."
  }
}

═══════════════════════════════════════════════════════════════════════════════
SKILL SETUP RULES
═══════════════════════════════════════════════════════════════════════════════

Each entry in skill_setups represents ONE LINK GROUP — gems that are LINKED TOGETHER in the same socket group. A single equipment slot (boots, helmet, etc.) can have MULTIPLE link groups.

CRITICAL: Every support gem in a link group must be compatible with the active skill(s) in that SAME group. Do NOT put unrelated gems in the same link group. For example:
- WRONG: Boots with [Whirling Blades, Faster Attacks, Cast when Damage Taken, Immortal Call] — CWDT cannot support Whirling Blades, Faster Attacks cannot support Immortal Call.
- RIGHT: Two separate setups in Boots:
  1. {"slot": "Boots", "label": "Movement", "gems": [Whirling Blades, Faster Attacks Support]}
  2. {"slot": "Boots", "label": "Guard", "gems": [Cast when Damage Taken Support, Immortal Call]}

Common link group patterns:
- Movement: [Whirling Blades / Shield Charge / Flame Dash] + [Faster Attacks Support / Arcane Surge Support]
- Guard (CWDT): [Cast when Damage Taken Support (lvl 1-3)] + [Immortal Call / Steelskin / Molten Shell (matching low level)]
- Curse: [Blasphemy Support + Hex] or [Mark On Hit Support + Mark] or self-cast
- Auras: [Aura 1 + Aura 2 + Enlighten Support] — auras don't need to "support" each other, they just share the link for Enlighten
- Golem: Often just a 1-link [Summon Lightning Golem] or with [Minion Life Support]

═══════════════════════════════════════════════════════════════════════════════
FINAL CHECKLIST (do this before outputting)
═══════════════════════════════════════════════════════════════════════════════

1. Re-read the user's prompt. Did they exclude anything (no totems, no minions, SSF only, specific ascendancy)? Respect ALL constraints.
2. For EACH link group: does every support gem have compatible tags with the active skill in that group? If not, move the gem to a different group or remove it.
3. Are unrelated skills in separate link groups? CWDT+Guard should NEVER be in the same group as a movement skill.
4. If using Elemental Overload or Resolute Technique: are there ANY crit gems or crit jewel mods? Remove them.
5. If using Chaos Inoculation: are there ANY life-scaling passives, jewel mods, or life flasks? Remove them.
6. Are all jewel mod values within the caps above?
7. Does every passive notable actually benefit this specific build?
8. Gem names must match exactly how they appear in Path of Building (e.g. "Concentrated Effect Support" not "Concentrated Effect").
9. Provide 2-4 jewels. Base type should match the build's primary attribute: Crimson (str), Viridian (dex), Cobalt (int).
10. In "playstyle", "summary", and "passive_path_description": ONLY describe mechanics that are actually present in the build. Do NOT mention Fork if Fork Support is not linked. Do NOT mention Chain if Chain Support is not linked. Do NOT mention Pierce if Pierce Support is not linked. Do NOT reference gem interactions, ascendancy nodes, or item effects that are not part of the build you are outputting. Every claim must be backed by a gem, passive, or gear choice you actually included.
"""


@app.post("/api/generate")
async def generate_build(req: GenerateRequest):
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set in environment.")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": req.prompt}],
        )
    except anthropic.APIError as e:
        logger.error(f"Anthropic API error: {e}")
        raise HTTPException(status_code=502, detail=f"Claude API error: {str(e)}")

    raw_text = message.content[0].text.strip()
    logger.info(f"Claude response length: {len(raw_text)} chars, stop_reason: {message.stop_reason}")

    # Strip any accidental markdown fences
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1]
        raw_text = raw_text.rsplit("```", 1)[0].strip()

    # Try to extract JSON if Claude added text before/after it
    if not raw_text.startswith("{"):
        json_start = raw_text.find("{")
        if json_start != -1:
            raw_text = raw_text[json_start:]
    if not raw_text.endswith("}"):
        json_end = raw_text.rfind("}")
        if json_end != -1:
            raw_text = raw_text[:json_end + 1]

    try:
        build_data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude response as JSON: {e}\nRaw start: {raw_text[:300]}\nRaw end: {raw_text[-300:]}")
        raise HTTPException(status_code=502, detail="Claude returned invalid JSON. Please try again.")

    # ── Detect user constraints from the prompt ─────────────────────────
    user_constraints = _detect_constraints(req.prompt)

    # ── Enforce class/ascendancy if user explicitly requested one ────────
    build_fixes = []
    if "force_class" in user_constraints:
        forced_class = user_constraints["force_class"]
        if build_data.get("class_name") != forced_class:
            old_class = build_data.get("class_name", "Unknown")
            build_data["class_name"] = forced_class
            build_fixes.append({
                "type": "constraint",
                "severity": "fixed",
                "message": f"Changed class from '{old_class}' to '{forced_class}' — user requested {forced_class}",
            })
            # If ascendancy no longer valid for this class, pick the first one
            class_meta = CLASS_INFO.get(forced_class, {})
            valid_ascs = set(class_meta.get("ascendancies", {}).keys())
            if build_data.get("ascendancy_name") not in valid_ascs:
                old_asc = build_data.get("ascendancy_name", "")
                # If user also forced an ascendancy and it's valid for this class, use it
                if "force_ascendancy" in user_constraints and user_constraints["force_ascendancy"] in valid_ascs:
                    new_asc = user_constraints["force_ascendancy"]
                else:
                    new_asc = next(iter(valid_ascs)) if valid_ascs else ""
                build_data["ascendancy_name"] = new_asc
                build_fixes.append({
                    "type": "constraint",
                    "severity": "fixed",
                    "message": f"Changed ascendancy from '{old_asc}' to '{new_asc}' — must match {forced_class}",
                })

    if "force_ascendancy" in user_constraints and "force_class" not in user_constraints:
        forced_asc = user_constraints["force_ascendancy"]
        if build_data.get("ascendancy_name") != forced_asc:
            old_asc = build_data.get("ascendancy_name", "Unknown")
            build_data["ascendancy_name"] = forced_asc
            # Also fix the class to match
            correct_class = ASCENDANCY_TO_CLASS.get(forced_asc, build_data.get("class_name", "Scion"))
            build_data["class_name"] = correct_class
            build_fixes.append({
                "type": "constraint",
                "severity": "fixed",
                "message": f"Changed ascendancy from '{old_asc}' to '{forced_asc}' — user requested {forced_asc}",
            })

    # ── Auto-fix the build: remove bad gems, cap jewel mods, etc. ────────
    gem_db = get_gem_db()
    build_fixes.extend(fix_and_validate_build(build_data, gem_db, user_constraints=user_constraints))

    # Log fixes
    for f in build_fixes:
        logger.info(f"Build auto-fix: {f['message']}")

    # Build notes string for the POB XML
    notes = _format_notes(build_data)
    build_data["notes"] = notes

    # Generate POB XML + import code
    try:
        xml_str, import_code = generate_import_code(build_data)
    except Exception as e:
        import traceback
        logger.error(f"POB generation error: {e}\n{traceback.format_exc()}")
        import_code = ""

    # Extract pathfinder debug info (set by pob_utils during XML generation)
    matched_nodes = build_data.pop("_matched_nodes", [])
    unmatched_nodes = build_data.pop("_unmatched_nodes", [])
    total_allocated = build_data.pop("_total_nodes_allocated", 0)
    jewel_sockets_used = build_data.pop("_jewel_sockets_used", 0)

    return JSONResponse({
        "build": build_data,
        "import_code": import_code,
        "tree_info": {
            "total_nodes": total_allocated,
            "matched": matched_nodes,
            "unmatched": unmatched_nodes,
            "jewel_sockets": jewel_sockets_used,
        },
        "fixes": [f for f in build_fixes],
    })


def _detect_constraints(prompt: str) -> dict:
    """
    Parse the user's prompt for explicit build constraints.
    Returns a dict of constraint flags.
    """
    p = prompt.lower()
    constraints = {}

    # "no totems" / "without totems" / "no totem" etc.
    if any(phrase in p for phrase in ["no totem", "not totem", "without totem",
                                      "don't want totem", "no warchief",
                                      "no protector", "no ballista"]):
        constraints["no_totems"] = True

    if any(phrase in p for phrase in ["no mine", "not mine", "without mine",
                                      "no mines"]):
        constraints["no_mines"] = True

    if any(phrase in p for phrase in ["no trap", "not trap", "without trap",
                                      "no traps"]):
        constraints["no_traps"] = True

    if any(phrase in p for phrase in ["no minion", "not minion", "without minion",
                                      "no summon", "no zombies", "no spectres",
                                      "no skeletons"]):
        constraints["no_minions"] = True

    # ── Detect explicit class/ascendancy requests ────────────────────────
    CLASS_NAMES = {
        "scion": "Scion", "marauder": "Marauder", "ranger": "Ranger",
        "witch": "Witch", "duelist": "Duelist", "templar": "Templar", "shadow": "Shadow",
    }
    ASCENDANCY_NAMES = {
        "ascendant": "Ascendant",
        "juggernaut": "Juggernaut", "berserker": "Berserker", "chieftain": "Chieftain",
        "deadeye": "Deadeye", "raider": "Raider", "pathfinder": "Pathfinder",
        "occultist": "Occultist", "necromancer": "Necromancer", "elementalist": "Elementalist",
        "slayer": "Slayer", "gladiator": "Gladiator", "champion": "Champion",
        "inquisitor": "Inquisitor", "hierophant": "Hierophant", "guardian": "Guardian",
        "assassin": "Assassin", "saboteur": "Saboteur", "trickster": "Trickster",
    }

    # Use word boundaries to avoid false matches (e.g. "pathfinder" in "pathfinding")
    words = set(re.findall(r'\b\w+\b', p))
    for key, name in CLASS_NAMES.items():
        if key in words:
            constraints["force_class"] = name
            break

    for key, name in ASCENDANCY_NAMES.items():
        if key in words:
            constraints["force_ascendancy"] = name
            break

    return constraints


def _format_notes(b: dict) -> str:
    lines = [
        f"=== {b.get('build_name', 'Generated Build')} ===",
        "",
        b.get("summary", ""),
        "",
        f"Class: {b.get('class_name', '')} / {b.get('ascendancy_name', '')}",
        f"Budget: {b.get('budget', '').replace('_', ' ').title()}",
        f"Bandit: {b.get('bandit', 'Kill All')}",
        f"Pantheon: {b.get('pantheon_major', '')} + {b.get('pantheon_minor', '')}",
        "",
        "── PLAYSTYLE ──",
        b.get("playstyle", ""),
        "",
        "── STRENGTHS ──",
    ]
    for s in b.get("strengths", []):
        lines.append(f"+ {s}")
    lines += ["", "── WEAKNESSES ──"]
    for w in b.get("weaknesses", []):
        lines.append(f"- {w}")
    lines += ["", "── PASSIVE TREE ──", b.get("passive_path_description", ""), ""]

    lines += ["── LEVELING GEMS ──"]
    for step in b.get("gem_leveling", []):
        lines.append(f"[Level {step.get('level', '?')}] {step.get('action', '')}")
    lines += ["", "── GEAR GUIDE ──"]
    for slot, desc in b.get("gear_guide", {}).items():
        lines.append(f"{slot.upper()}: {desc}")
    lines += ["", "── Generated by POE AI Build Generator ──"]
    return "\n".join(lines)
