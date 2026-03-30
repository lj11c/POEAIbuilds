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

from data_parser import load_data
from pob_utils import generate_import_code
from tree_pathfinder import get_tree

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


SYSTEM_PROMPT = """You are an expert Path of Exile 1 build planner with deep knowledge of all patches up to 3.28.

When the user describes what kind of build they want, you respond with a complete, viable build as a JSON object.
Always output ONLY valid JSON — no markdown fences, no extra text before or after.

CRITICAL — Before choosing passives, you MUST first commit to these build-defining decisions:
1. DAMAGE TYPE: Pick ONE primary damage type (physical, fire, cold, lightning, chaos, or a specific combo like "physical converted to fire"). ALL offensive passives must support this.
2. WEAPON TYPE: Pick ONE specific weapon type if attack-based (e.g. "two-handed sword", "dual-wield claws", "bow", "wand+shield"). NEVER mix weapon types — if you pick swords, do NOT take axe or mace nodes.
3. ATTACK STYLE: Pick ONE — two-handed, dual-wield, one-hand+shield, or not applicable (spells/minions). If two-handed, take ONLY two-handed passives, never one-hand or dual-wield. If dual-wield, take ONLY dual-wield passives.
4. DEFENSE LAYERS: Pick 1-2 primary defenses (life+armour, ES+evasion, life+evasion, MoM, CI, etc.). All defensive passives must support these layers.
5. SKILL TYPE: Attack, spell, minion, trap/mine, totem, or DOT-focused.

Every passive notable you select MUST directly benefit the build's chosen damage type, weapon type, attack style, or defense layers. Do NOT take generic "seems useful" nodes that conflict with your core choices.

The JSON must match this exact schema:

{
  "build_name": "Short evocative name, e.g. 'Elementalist Cremation'",
  "summary": "2-3 sentence overview of the build concept and why it works",
  "class_name": "One of: Scion, Marauder, Ranger, Witch, Duelist, Templar, Shadow",
  "ascendancy_name": "The exact ascendancy name, e.g. 'Elementalist'",
  "level": 90,
  "damage_type": "The primary damage type this build scales, e.g. 'Cold Damage over Time', 'Physical', 'Lightning'",
  "weapon_type": "The specific weapon setup, e.g. 'Two-Handed Sword', 'Dual-Wield Claws', 'Wand + Shield', 'Staff', 'Bow', 'N/A (spell caster)'",
  "attack_style": "Two-Handed OR Dual-Wield OR One-Hand + Shield OR Bow OR Spell/Caster OR Minions",
  "playstyle": "2-3 sentences describing how to play the build moment-to-moment",
  "strengths": ["up to 4 short bullet strings"],
  "weaknesses": ["up to 3 short bullet strings"],
  "budget": "league_starter OR low OR mid OR high OR mirror",
  "bandit": "Kill All OR Alira OR Oak OR Kraityn",
  "pantheon_major": "e.g. Soul of Arakaali",
  "pantheon_minor": "e.g. Soul of Shakari",
  "skill_setups": [
    {
      "slot": "Body Armour",
      "is_main": true,
      "label": "Main Skill",
      "gems": [
        {"name": "Exact Gem Name", "level": 21, "quality": 20, "is_support": false},
        {"name": "Swift Affliction Support", "level": 20, "quality": 20, "is_support": true}
      ]
    },
    {
      "slot": "Helmet",
      "is_main": false,
      "label": "Auras",
      "gems": [
        {"name": "Malevolence", "level": 20, "quality": 0, "is_support": false},
        {"name": "Zealotry", "level": 20, "quality": 0, "is_support": false},
        {"name": "Enlighten Support", "level": 3, "quality": 0, "is_support": true}
      ]
    }
  ],
  "passive_notables": [
    "List of notable passive and keystone names from the PoE 1 passive tree.",
    "Include 20-30 key notables and keystones the build relies on.",
    "Use exact names as they appear in-game, e.g. 'Elemental Overload', 'Whispers of Doom', 'Heart of Thunder'.",
    "Do NOT include minor/small passives — the system will automatically compute the connecting path.",
    "Include notables from ALL regions of the tree the build paths through.",
    "EVERY notable must be consistent with the build's damage_type, weapon_type, and attack_style.",
    "For example, a Two-Handed Sword build should take 'Blade of Cunning' and 'Splitting Strikes' but NEVER 'Ambidexterity' (dual wield) or 'Destroyer' (maces)."
  ],
  "passive_path_description": "3-5 sentences describing the routing of the passive tree: where to start, which clusters to reach first, major keystones, etc.",
  "gem_leveling": [
    {"level": 1,  "action": "Start with X + Y + Z. Pick up A on the way."},
    {"level": 12, "action": "Swap to B. Add C support."},
    {"level": 28, "action": "Unlock your main skill. Set up your 4-link."},
    {"level": 38, "action": "..."},
    {"level": 60, "action": "Full 6-link goal. Swap to mapping setup."}
  ],
  "jewels": [
    {
      "name": "A creative rare jewel name",
      "base": "One of: Cobalt Jewel, Crimson Jewel, Viridian Jewel",
      "mods": [
        "7% increased maximum Life",
        "One mod matching the build's damage type",
        "One mod matching the build's offense (attack speed, crit multi, etc.)",
        "One defensive or utility mod"
      ]
    }
  ],
  "gear_guide": {
    "helmet": "What to look for and any uniques to aim for",
    "body_armour": "...",
    "gloves": "...",
    "boots": "...",
    "weapon": "...",
    "offhand": "...",
    "amulet": "...",
    "rings": "...",
    "belt": "...",
    "flasks": "..."
  }
}

Guidelines:
- Make genuinely good builds that work in the current PoE 1 meta or are well-established archetypes.
- For league starters, avoid items that require trading for specific rare uniques.
- Include all 6 gems in the main 6-link if the build is endgame.
- gem names must match exactly how they appear in Path of Building (e.g. 'Concentrated Effect Support' not just 'Concentrated Effect').
- passive_notables is critical — the system uses these names to compute the full passive tree path with all connecting nodes automatically. Include enough notables (20-30) to define the build's tree shape.
- COHERENCE CHECK: Before outputting, review every notable in your list and ask "does this benefit my chosen damage_type, weapon_type, and attack_style?" Remove any that don't.
- Do not take weapon-specific nodes for weapons the build does not use.
- Do not mix two-handed passives with dual-wield or one-hand passives.
- Do not take spell passives on an attack build or vice versa (unless there's a specific mechanical reason).
- Always provide the complete JSON with all fields.
- For jewels: provide 2-4 jewel definitions. The system will automatically socket them into jewel slots on the passive tree that the build paths near.
  - Choose the right jewel base: Crimson (str), Viridian (dex), Cobalt (int) — pick the one matching the build's main attribute.
  - Use REAL PoE 1 jewel mods. Common good mods: "7% increased maximum Life", "+12% to Global Critical Strike Multiplier", "10% increased Attack Speed", "15% increased Fire Damage", "+15% to all Elemental Resistances", etc.
  - Each jewel should have 3-4 mods that are all relevant to the build.
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
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": req.prompt}],
        )
    except anthropic.APIError as e:
        logger.error(f"Anthropic API error: {e}")
        raise HTTPException(status_code=502, detail=f"Claude API error: {str(e)}")

    raw_text = message.content[0].text.strip()

    # Strip any accidental markdown fences
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1]
        raw_text = raw_text.rsplit("```", 1)[0].strip()

    try:
        build_data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude response as JSON: {e}\nRaw: {raw_text[:500]}")
        raise HTTPException(status_code=502, detail="Claude returned invalid JSON. Please try again.")

    # Build notes string for the POB XML
    notes = _format_notes(build_data)
    build_data["notes"] = notes

    # Generate POB XML + import code
    try:
        xml_str, import_code = generate_import_code(build_data)
    except Exception as e:
        logger.error(f"POB generation error: {e}")
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
    })


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
