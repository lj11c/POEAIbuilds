"""
Gem compatibility validator.

Parses POB's skill data to validate that support gems can actually
support the active skills they're linked with, and flags other
common build coherence issues.
"""

import re
import os
import logging

logger = logging.getLogger(__name__)

POB_DIR = os.path.join(os.path.dirname(__file__), "POB", "PathOfBuilding-dev")
SKILLS_DIR = os.path.join(POB_DIR, "src", "Data", "Skills")
GEMS_LUA = os.path.join(POB_DIR, "src", "Data", "Gems.lua")


# ── SkillType enum (parsed from Global.lua at import time) ───────────────────
GLOBAL_LUA = os.path.join(POB_DIR, "src", "Data", "Global.lua")

def _parse_skill_type_enum() -> dict[str, int]:
    """Parse the full SkillType enum from Global.lua."""
    types = {}
    try:
        with open(GLOBAL_LUA, encoding="utf-8") as f:
            content = f.read()
        # Find the SkillType = { ... } block
        m = re.search(r'SkillType\s*=\s*\{(.*?)\}', content, re.DOTALL)
        if m:
            for entry in re.finditer(r'(\w+)\s*=\s*(\d+)', m.group(1)):
                name = entry.group(1)
                val = int(entry.group(2))
                if not name.startswith("Removed"):  # skip "Removed6", "Removed8", etc.
                    types[name] = val
    except FileNotFoundError:
        logger.error(f"Global.lua not found at {GLOBAL_LUA}")
    logger.info(f"Parsed {len(types)} SkillType entries from Global.lua")
    return types

SKILL_TYPE = _parse_skill_type_enum()

# Reverse lookup
SKILL_TYPE_BY_ID = {v: k for k, v in SKILL_TYPE.items()}


class GemDatabase:
    """Parsed database of all active and support gems with compatibility info."""

    def __init__(self):
        # gem name (lowercase) → {tags: set, skill_types: set, weapon_types: set}
        self.active_skills: dict[str, dict] = {}
        # gem name (lowercase) → {require: set, exclude: set, description: str}
        self.support_gems: dict[str, dict] = {}
        # gem name (lowercase) → tag string for display
        self.gem_tags: dict[str, str] = {}
        # set of all known gem names (lowercase)
        self.all_gem_names: set[str] = set()

    def load(self):
        """Parse all gem and skill data files."""
        self._parse_gem_tags()
        self._parse_skill_files()
        logger.info(f"GemDB loaded: {len(self.active_skills)} active skills, "
                     f"{len(self.support_gems)} support gems")

    def _parse_gem_tags(self):
        """Parse Gems.lua to get tag strings for all gems."""
        with open(GEMS_LUA, encoding="utf-8") as f:
            content = f.read()

        # Match gem entries: name = "...", ... tagString = "...",
        for m in re.finditer(
            r'name\s*=\s*"([^"]+)".*?tagString\s*=\s*"([^"]+)"',
            content, re.DOTALL
        ):
            name = m.group(1)
            tags = m.group(2)
            key = name.lower()
            self.gem_tags[key] = tags
            self.all_gem_names.add(key)

    def _parse_skill_files(self):
        """Parse sup_*.lua and act_*.lua to get skill types and support requirements."""
        for fname in os.listdir(SKILLS_DIR):
            if not fname.endswith(".lua"):
                continue
            filepath = os.path.join(SKILLS_DIR, fname)
            with open(filepath, encoding="utf-8") as f:
                content = f.read()

            if fname.startswith("sup_"):
                self._parse_supports(content)
            elif fname.startswith("act_"):
                self._parse_actives(content)

    def _parse_supports(self, content: str):
        """Parse a support gem file for requireSkillTypes and excludeSkillTypes."""
        # Split on skills["..."] blocks
        blocks = re.split(r'skills\["[^"]+"\]\s*=\s*\{', content)
        name_re = re.compile(r'name\s*=\s*"([^"]+)"')
        req_re = re.compile(r'requireSkillTypes\s*=\s*\{([^}]*)\}')
        exc_re = re.compile(r'excludeSkillTypes\s*=\s*\{([^}]*)\}')
        desc_re = re.compile(r'description\s*=\s*"([^"]+)"')

        for block in blocks[1:]:  # skip preamble
            name_m = name_re.search(block)
            if not name_m:
                continue
            name = name_m.group(1)
            key = name.lower()

            # Check this is actually a support
            if 'support = true' not in block:
                continue

            # Use lists (not sets) to preserve postfix expression ordering
            req_types: list[int] = []
            req_m = req_re.search(block)
            if req_m:
                for st in re.findall(r'SkillType\.(\w+)', req_m.group(1)):
                    if st in SKILL_TYPE:
                        req_types.append(SKILL_TYPE[st])

            exc_types: list[int] = []
            exc_m = exc_re.search(block)
            if exc_m:
                for st in re.findall(r'SkillType\.(\w+)', exc_m.group(1)):
                    if st in SKILL_TYPE:
                        exc_types.append(SKILL_TYPE[st])

            desc = ""
            desc_m = desc_re.search(block)
            if desc_m:
                desc = desc_m.group(1)

            self.support_gems[key] = {
                "name": name,
                "require": req_types,
                "exclude": exc_types,
                "description": desc,
            }

    def _parse_actives(self, content: str):
        """Parse an active skill file for skillTypes and weaponTypes."""
        blocks = re.split(r'skills\["[^"]+"\]\s*=\s*\{', content)
        name_re = re.compile(r'name\s*=\s*"([^"]+)"')
        st_re = re.compile(r'skillTypes\s*=\s*\{([^}]*)\}')
        wt_re = re.compile(r'weaponTypes\s*=\s*\{([^}]*)\}')

        for block in blocks[1:]:
            name_m = name_re.search(block)
            if not name_m:
                continue
            name = name_m.group(1)
            key = name.lower()

            # Skip supports that ended up in active files
            if 'support = true' in block:
                continue

            skill_types = set()
            st_m = st_re.search(block)
            if st_m:
                for st in re.findall(r'SkillType\.(\w+)', st_m.group(1)):
                    if st in SKILL_TYPE:
                        skill_types.add(SKILL_TYPE[st])

            weapon_types = set()
            wt_m = wt_re.search(block)
            if wt_m:
                for wt in re.findall(r'"([^"]+)"', wt_m.group(1)):
                    weapon_types.add(wt.lower())

            self.active_skills[key] = {
                "name": name,
                "skill_types": skill_types,
                "weapon_types": weapon_types,
            }

    def can_support(self, active_name: str, support_name: str) -> tuple[bool, str]:
        """
        Check if a support gem can support an active skill.
        Returns (compatible, reason).

        POB uses a postfix expression evaluator for requireSkillTypes:
        - Types are pushed to a stack as booleans (does the skill have this type?)
        - SkillType.OR pops two and pushes (a or b)
        - SkillType.AND pops two and pushes (a and b)
        - SkillType.NOT pops one and pushes (not a)
        - If no operators, each type is independent — ANY true in the stack passes
        """
        active_key = active_name.lower().strip()
        # Support gem names in our data don't have " Support" suffix
        support_key = support_name.lower().strip()
        support_key_short = support_key.replace(" support", "").strip()

        active = self.active_skills.get(active_key)
        support = (self.support_gems.get(support_key)
                   or self.support_gems.get(support_key_short))

        if not active or not support:
            return True, ""  # Can't validate, assume OK

        active_types = active["skill_types"]
        req_types = support["require"]
        exc_types = support["exclude"]

        # Evaluate requireSkillTypes as a postfix expression (matching POB logic)
        if req_types:
            if not self._eval_type_expression(req_types, active_types):
                type_names = [SKILL_TYPE_BY_ID.get(t, str(t)) for t in req_types
                              if t not in (SKILL_TYPE["OR"], SKILL_TYPE["AND"], SKILL_TYPE["NOT"])]
                return False, f"skill lacks required tags: {', '.join(type_names)}"

        # Evaluate excludeSkillTypes
        if exc_types:
            if self._eval_type_expression(exc_types, active_types):
                type_names = [SKILL_TYPE_BY_ID.get(t, str(t)) for t in exc_types
                              if t not in (SKILL_TYPE["OR"], SKILL_TYPE["AND"], SKILL_TYPE["NOT"])]
                return False, f"excluded by tags: {', '.join(type_names)}"

        return True, ""

    @staticmethod
    def _eval_type_expression(check_types: list[int], skill_types: set[int]) -> bool:
        """
        Evaluate a postfix type expression (matching POB's doesTypeExpressionMatch).
        Returns True if the expression matches.
        """
        OR = SKILL_TYPE["OR"]
        AND = SKILL_TYPE["AND"]
        NOT = SKILL_TYPE["NOT"]

        stack: list[bool] = []
        for st in check_types:
            if st == OR:
                if len(stack) >= 2:
                    b = stack.pop()
                    stack[-1] = stack[-1] or b
            elif st == AND:
                if len(stack) >= 2:
                    b = stack.pop()
                    stack[-1] = stack[-1] and b
            elif st == NOT:
                if stack:
                    stack[-1] = not stack[-1]
            else:
                stack.append(st in skill_types)

        # Any True in the stack means the expression matches
        return any(stack)

    def find_replacement_supports(self, active_name: str,
                                   existing_gems: set[str],
                                   damage_type: str = "",
                                   weapon_type: str = "",
                                   keystones: set[str] | None = None,
                                   count: int = 1) -> list[str]:
        """
        Find valid replacement support gems for an active skill.
        Returns up to `count` support gem names that:
          - Can support the active skill
          - Are not already in existing_gems
          - Are not removed/Awakened gems
          - Are ranked by relevance to the build's damage type
        """
        active_key = active_name.lower().strip()
        active = self.active_skills.get(active_key)
        if not active:
            return []

        keystones = keystones or set()
        damage_type = damage_type.lower()
        weapon_type = weapon_type.lower()
        existing_lower = {g.lower().strip() for g in existing_gems}

        # Build list of bad gem keywords from keystones
        bad_keywords = set()
        for ks_name, rules in KEYSTONE_CONFLICTS.items():
            if ks_name in keystones:
                for bad in rules.get("bad_gems", []):
                    bad_keywords.add(bad)

        candidates: list[tuple[int, str]] = []  # (priority, name)

        for sup_key, sup_info in self.support_gems.items():
            sup_name = sup_info["name"]
            full_name = sup_name if sup_name.lower().endswith(" support") else sup_name + " Support"
            full_key = full_name.lower()

            # Skip if already in the setup
            if full_key in existing_lower or sup_key in existing_lower:
                continue

            # Skip Awakened / removed gems
            if full_key.startswith("awakened ") or full_key in REMOVED_GEMS:
                continue

            # Skip keystone-conflicting gems
            if any(bad in full_key for bad in bad_keywords):
                continue

            # Check compatibility
            compatible, _ = self.can_support(active_name, sup_name)
            if not compatible:
                continue

            # Rank by relevance to build
            priority = _rank_support(sup_name, sup_info.get("description", ""),
                                      damage_type, weapon_type, active)
            candidates.append((priority, full_name))

        # Sort by priority (lower = better), take top N
        candidates.sort(key=lambda x: x[0])
        return [name for _, name in candidates[:count]]

    def get_skill_tags(self, gem_name: str) -> str:
        """Get the tag string for a gem (e.g. 'Attack, Melee, Strike, Lightning')."""
        return self.gem_tags.get(gem_name.lower().strip(), "")

    def gem_exists(self, gem_name: str) -> bool:
        """Check if a gem name exists in the database."""
        key = gem_name.lower().strip()
        # Check both the gems list and active/support skill lists
        return (key in self.all_gem_names
                or key in self.active_skills
                or key in self.support_gems
                or key.replace(" support", "") in self.support_gems)


# ── Support gem ranking for replacements ─────────────────────────────────────

def _rank_support(name: str, description: str, damage_type: str,
                  weapon_type: str, active_info: dict) -> int:
    """
    Rank a support gem for replacement priority (lower = better fit).
    Considers damage type, weapon type, and active skill tags.
    """
    name_lower = name.lower()
    desc_lower = description.lower()
    skill_types = active_info.get("skill_types", set())
    score = 50  # neutral baseline

    # ── Damage type match (big bonus) ──
    if damage_type:
        if "fire" in damage_type:
            if "fire" in name_lower or "combustion" in name_lower or "burning" in name_lower:
                score -= 30
            if "cold" in name_lower or "lightning" in name_lower or "chaos" in name_lower:
                score += 20
        elif "cold" in damage_type:
            if "cold" in name_lower or "hypothermia" in name_lower:
                score -= 30
            if "fire" in name_lower or "lightning" in name_lower or "chaos" in name_lower:
                score += 20
        elif "lightning" in damage_type:
            if "lightning" in name_lower or "innervate" in name_lower:
                score -= 30
            if "fire" in name_lower or "cold" in name_lower or "chaos" in name_lower:
                score += 20
        elif "chaos" in damage_type:
            if "chaos" in name_lower or "void" in name_lower or "wither" in name_lower:
                score -= 30
            if "fire" in name_lower or "cold" in name_lower or "lightning" in name_lower:
                score += 20
        elif "physical" in damage_type:
            if "physical" in name_lower or "brutality" in name_lower or "impale" in name_lower:
                score -= 30

    # ── Skill type match ──
    ATTACK = SKILL_TYPE.get("Attack", 1)
    SPELL = SKILL_TYPE.get("Spell", 2)
    MELEE = SKILL_TYPE.get("Melee", 24)
    PROJECTILE = SKILL_TYPE.get("Projectile", 3)
    AREA = SKILL_TYPE.get("Area", 11)
    DOT = SKILL_TYPE.get("DamageOverTime", 39)

    if ATTACK in skill_types:
        if "attack" in desc_lower or "attacks" in desc_lower:
            score -= 10
        if "spell" in name_lower and "spell" not in desc_lower:
            score += 30
    if SPELL in skill_types:
        if "spell" in desc_lower:
            score -= 10
        if "melee" in name_lower or "attack" in name_lower:
            score += 30
    if MELEE in skill_types:
        if "melee" in name_lower:
            score -= 15
    if PROJECTILE in skill_types:
        if "projectile" in name_lower or "pierce" in name_lower or "chain" in name_lower:
            score -= 10
    if AREA in skill_types:
        if "area" in name_lower or "concentrated" in name_lower:
            score -= 10
    if DOT in skill_types:
        if "affliction" in name_lower or "decay" in name_lower or "ailment" in name_lower:
            score -= 15

    # ── Universal good supports get slight bonus ──
    universally_good = [
        "added fire damage", "added cold damage", "added lightning damage",
        "elemental damage with attacks", "increased critical damage",
        "increased critical strikes", "faster attacks", "faster casting",
        "concentrated effect", "increased area of effect", "life leech",
        "energy leech", "inspiration", "trinity",
    ]
    for ug in universally_good:
        if ug in name_lower:
            score -= 5
            break

    # ── Niche/utility supports are lower priority ──
    niche = [
        "knockback", "stun", "blind", "culling", "onslaught",
        "chance to flee", "block chance reduction",
    ]
    for n in niche:
        if n in name_lower:
            score += 15
            break

    return score


# ── Keystone conflict rules ──────────────────────────────────────────────────

KEYSTONE_CONFLICTS = {
    "elemental overload": {
        "bad_gems": [
            "increased critical strikes support",
            "increased critical damage support",
            "power charge on critical support",
            "assassin's mark",  # only bad if used for crit scaling
        ],
        "bad_jewel_mods": [
            "critical strike multiplier",
            "critical strike chance",
        ],
        "bad_passive_keywords": ["crit", "critical"],
        "reason": "Elemental Overload sets crit multiplier to 0 — crit scaling is wasted",
    },
    "resolute technique": {
        "bad_gems": [
            "increased critical strikes support",
            "increased critical damage support",
            "power charge on critical support",
            "assassin's mark",
        ],
        "bad_jewel_mods": [
            "critical strike multiplier",
            "critical strike chance",
        ],
        # Notable/keystone names whose presence alongside RT is contradictory.
        # RT makes crits impossible, so any crit-scaling notable is wasted.
        "bad_passive_keywords": ["crit", "critical"],
        "reason": "Resolute Technique makes attacks never crit — crit scaling is wasted",
    },
    "ghost reaver": {
        "bad_passive_keywords": ["maximum life", "life leech"],
        "reason": "Ghost Reaver converts leech to ES — life investment and life leech notables are wasted",
    },
    "chaos inoculation": {
        "bad_jewel_mods": [
            "maximum life",
            "increased maximum life",
        ],
        "bad_passive_keywords": ["maximum life", "life leech"],
        "reason": "CI sets max life to 1 — life scaling is useless",
    },
    "blood magic": {
        "bad_gems": [
            "clarity", "discipline",
        ],
        "reason": "Blood Magic removes mana — mana-related effects are useless",
    },
    "ancestral bond": {
        "reason": "Ancestral Bond prevents you from dealing damage directly — you deal damage through totems only",
    },
}


# ── Jewel mod validation ─────────────────────────────────────────────────────

# Maximum values for common jewel mods in PoE 1
JEWEL_MOD_CAPS = {
    "increased attack speed": 5,
    "increased cast speed": 5,
    "increased maximum life": 7,
    "increased maximum energy shield": 8,
    "to global critical strike multiplier": 18,
    "increased critical strike chance": 15,
    "increased physical damage": 16,
    "increased fire damage": 16,
    "increased cold damage": 16,
    "increased lightning damage": 16,
    "increased chaos damage": 16,
    "increased spell damage": 16,
    "increased melee damage": 16,
    "increased projectile damage": 10,
    "increased area damage": 10,
    "increased damage": 12,
    "to all elemental resistances": 12,
    "to fire resistance": 18,
    "to cold resistance": 18,
    "to lightning resistance": 18,
    "to chaos resistance": 13,
    "increased attack speed with": 3,  # weapon-specific caps are lower
    "increased mana regeneration rate": 20,
}


def validate_jewel_mod(mod: str) -> tuple[bool, str]:
    """
    Validate a jewel mod string against known caps.
    Returns (valid, suggestion).
    """
    mod_lower = mod.lower()

    # Extract numeric value
    num_m = re.search(r'(\d+)%?', mod)
    if not num_m:
        return True, ""

    value = int(num_m.group(1))

    for pattern, cap in JEWEL_MOD_CAPS.items():
        if pattern in mod_lower:
            if value > cap:
                fixed = mod_lower.replace(str(value), str(cap), 1)
                return False, f"Value {value}% exceeds jewel cap of {cap}% — use: {fixed}"

    return True, ""


def fix_jewel_mod(mod: str) -> str:
    """Fix a jewel mod by capping its value to the max allowed."""
    mod_lower = mod.lower()

    num_m = re.search(r'(\d+)', mod)
    if not num_m:
        return mod

    value = int(num_m.group(1))

    for pattern, cap in JEWEL_MOD_CAPS.items():
        if pattern in mod_lower:
            if value > cap:
                return mod.replace(str(value), str(cap), 1)

    return mod


# ── Removed / legacy skills ──────────────────────────────────────────────────

# Gems that were removed from PoE 1 but may still exist in POB data
# for backward compatibility. The AI should never suggest these.
REMOVED_GEMS = {
    # Awakened support gems — removed in 3.17 (Siege of the Atlas)
    "awakened added chaos damage support", "awakened added cold damage support",
    "awakened added fire damage support", "awakened added lightning damage support",
    "awakened ancestral call support", "awakened arrow nova support",
    "awakened blasphemy support", "awakened brutality support",
    "awakened burning damage support", "awakened cast on critical strike support",
    "awakened cast while channelling support", "awakened chain support",
    "awakened cold penetration support", "awakened controlled destruction support",
    "awakened deadly ailments support", "awakened elemental damage with attacks support",
    "awakened elemental focus support", "awakened empower support",
    "awakened enhance support", "awakened enlighten support",
    "awakened fire penetration support", "awakened fork support",
    "awakened generosity support", "awakened greater multiple projectiles support",
    "awakened hextouch support", "awakened increased area of effect support",
    "awakened lightning penetration support", "awakened melee physical damage support",
    "awakened melee splash support", "awakened minion damage support",
    "awakened multistrike support", "awakened spell cascade support",
    "awakened spell echo support", "awakened swift affliction support",
    "awakened unbound ailments support", "awakened unleash support",
    "awakened vicious projectiles support", "awakened void manipulation support",
    # Active skills removed from PoE 1
    "ancestral protector",
    "ancestral warchief",
    "decoy totem",
}

# Map removed gem → valid replacement
REMOVED_GEM_REPLACEMENTS = {
    "awakened added fire damage support": "Added Fire Damage Support",
    "awakened added cold damage support": "Added Cold Damage Support",
    "awakened added lightning damage support": "Added Lightning Damage Support",
    "awakened added chaos damage support": "Added Chaos Damage Support",
    "awakened brutality support": "Brutality Support",
    "awakened burning damage support": "Burning Damage Support",
    "awakened chain support": "Chain Support",
    "awakened cold penetration support": "Cold Penetration Support",
    "awakened controlled destruction support": "Controlled Destruction Support",
    "awakened deadly ailments support": "Deadly Ailments Support",
    "awakened elemental damage with attacks support": "Elemental Damage with Attacks Support",
    "awakened elemental focus support": "Elemental Focus Support",
    "awakened fire penetration support": "Fire Penetration Support",
    "awakened fork support": "Fork Support",
    "awakened generosity support": "Generosity Support",
    "awakened greater multiple projectiles support": "Greater Multiple Projectiles Support",
    "awakened increased area of effect support": "Increased Area of Effect Support",
    "awakened lightning penetration support": "Lightning Penetration Support",
    "awakened melee physical damage support": "Melee Physical Damage Support",
    "awakened melee splash support": "Melee Splash Support",
    "awakened minion damage support": "Minion Damage Support",
    "awakened multistrike support": "Multistrike Support",
    "awakened spell cascade support": "Spell Cascade Support",
    "awakened spell echo support": "Spell Echo Support",
    "awakened swift affliction support": "Swift Affliction Support",
    "awakened vicious projectiles support": "Vicious Projectiles Support",
    "awakened void manipulation support": "Void Manipulation Support",
    "awakened ancestral call support": "Ancestral Call Support",
    "awakened arrow nova support": "Arrow Nova Support",
    "awakened blasphemy support": "Blasphemy Support",
    "awakened cast on critical strike support": "Cast On Critical Strike Support",
    "awakened cast while channelling support": "Cast While Channelling Support",
    "awakened hextouch support": "Hextouch Support",
    "awakened unbound ailments support": "Unbound Ailments Support",
    "awakened unleash support": "Unleash Support",
    "awakened empower support": "Empower Support",
    "awakened enhance support": "Enhance Support",
    "awakened enlighten support": "Enlighten Support",
}


# ── Constraint keywords for detecting totem/mine/trap/minion gems ────────────

# SkillType IDs for constraint detection
_TOTEM_TYPE = SKILL_TYPE.get("SummonsTotem", 30)
_TRAP_TYPE = SKILL_TYPE.get("Trapped", 36)
_MINE_TYPE = SKILL_TYPE.get("RemoteMined", 40)
_MINION_TYPE = SKILL_TYPE.get("CreatesMinion", 21)

# Gem name keywords that indicate totem/mine/trap/minion skills
_TOTEM_KEYWORDS = {"totem", "protector", "warchief", "holy flame", "ballista",
                   "rejuvenation totem", "decoy totem", "siege ballista",
                   "shrapnel ballista", "artillery ballista"}
_MINE_KEYWORDS = {"mine", "remote mine", "blastchain", "high-impact"}
_TRAP_KEYWORDS = {"trap", "seismic trap", "explosive trap", "lightning trap",
                  "fire trap", "bear trap", "ice trap", "flamethrower trap"}
_MINION_KEYWORDS = {"zombie", "spectre", "skeleton", "summon", "raise",
                    "animate", "srs", "golem", "phantasm"}


def _is_constrained_gem(gem_name: str, gem_db: GemDatabase,
                        constraint_key: str) -> bool:
    """Check if a gem violates a specific constraint."""
    name_lower = gem_name.lower().strip()
    name_no_support = name_lower.replace(" support", "")

    # Check by gem name keywords
    if constraint_key == "no_totems":
        if any(kw in name_lower for kw in _TOTEM_KEYWORDS):
            return True
        if "multiple totems" in name_lower or "spell totem" in name_lower:
            return True
    elif constraint_key == "no_mines":
        if any(kw in name_lower for kw in _MINE_KEYWORDS):
            return True
    elif constraint_key == "no_traps":
        if any(kw in name_lower for kw in _TRAP_KEYWORDS):
            return True
        if "trap and mine damage" in name_lower:
            return True
    elif constraint_key == "no_minions":
        if any(kw in name_lower for kw in _MINION_KEYWORDS):
            return True
        if "minion" in name_lower:
            return True

    # Check by SkillType from the gem database
    active = gem_db.active_skills.get(name_lower) or gem_db.active_skills.get(name_no_support)
    if active:
        skill_types = active.get("skill_types", set())
        if constraint_key == "no_totems" and _TOTEM_TYPE in skill_types:
            return True
        if constraint_key == "no_mines" and _MINE_TYPE in skill_types:
            return True
        if constraint_key == "no_traps" and _TRAP_TYPE in skill_types:
            return True
        if constraint_key == "no_minions" and _MINION_TYPE in skill_types:
            return True

    return False


def _enforce_constraints(build_data: dict, gem_db: GemDatabase,
                         constraints: dict, fixes: list[dict]):
    """
    Remove entire skill setups that violate user constraints.
    Also removes individual support gems that enable constrained mechanics
    (e.g. Spell Totem Support when no_totems is set).
    """
    constraint_labels = {
        "no_totems": "totems",
        "no_mines": "mines",
        "no_traps": "traps",
        "no_minions": "minions",
    }

    setups = build_data.get("skill_setups", [])
    setups_to_remove = []

    for i, setup in enumerate(setups):
        gems = setup.get("gems", [])
        active_gems = [g for g in gems if not g.get("is_support")]
        support_gems = [g for g in gems if g.get("is_support")]

        # Check if any ACTIVE gem in this setup violates a constraint
        active_violates = False
        violated_constraint = ""
        for constraint_key, label in constraint_labels.items():
            if not constraints.get(constraint_key):
                continue
            for active in active_gems:
                if _is_constrained_gem(active["name"], gem_db, constraint_key):
                    active_violates = True
                    violated_constraint = label
                    break
            if active_violates:
                break

        if active_violates:
            # The main active skill is constrained — remove the entire setup
            gem_names = [g["name"] for g in gems]
            setup_label = setup.get("label", setup.get("slot", f"Setup {i+1}"))
            fixes.append({
                "type": "constraint",
                "severity": "fixed",
                "message": (f"Removed setup '{setup_label}' ({', '.join(gem_names)}) "
                            f"— user requested no {violated_constraint}"),
            })
            setups_to_remove.append(i)
            continue

        # Even if the active is fine, check supports that enable constrained
        # mechanics (e.g. Spell Totem Support, Trap Support, Mine Support)
        supports_to_remove = []
        for constraint_key, label in constraint_labels.items():
            if not constraints.get(constraint_key):
                continue
            for support in support_gems:
                if _is_constrained_gem(support["name"], gem_db, constraint_key):
                    supports_to_remove.append((support, label))

        for support, label in supports_to_remove:
            if support in gems:
                old_name = support["name"]
                gems.remove(support)
                fixes.append({
                    "type": "constraint",
                    "severity": "fixed",
                    "message": (f"Removed '{old_name}' from setup — "
                                f"user requested no {label}"),
                })

    # Remove flagged setups in reverse order to preserve indices
    for i in reversed(setups_to_remove):
        setups.pop(i)


# ── Build fix & validation ───────────────────────────────────────────────────

def fix_and_validate_build(build_data: dict, gem_db: GemDatabase,
                           user_constraints: dict | None = None) -> list[dict]:
    """
    Fix a build by removing incompatible gems, capping jewel mods, and
    removing conflicting jewel mods. Returns a list of fixes applied
    as {type, severity, message} dicts. After this runs the build
    should be clean.
    """
    fixes = []
    user_constraints = user_constraints or {}

    keystones = set()
    for name in build_data.get("passive_notables", []):
        keystones.add(name.lower())

    # ── Remove passive notables that contradict active keystones ─────────
    for ks_name, rules in KEYSTONE_CONFLICTS.items():
        bad_kw = rules.get("bad_passive_keywords", [])
        if not bad_kw or ks_name not in keystones:
            continue
        notables = build_data.get("passive_notables", [])
        removed = [n for n in notables
                   if n.lower() != ks_name
                   and any(kw in n.lower() for kw in bad_kw)]
        if removed:
            build_data["passive_notables"] = [n for n in notables if n not in removed]
            for n in removed:
                fixes.append({
                    "type": "passive",
                    "severity": "fixed",
                    "message": f"Removed passive '{n}' — contradicts {ks_name.title()} ({rules['reason']})",
                })

    # ── Enforce user constraints (no totems/mines/traps/minions) ────────
    if user_constraints:
        _enforce_constraints(build_data, gem_db, user_constraints, fixes)

    # ── Replace removed gems with their valid equivalents ────────────────
    # First pass: swap replaceable gems, mark setups to remove if active is gone
    setups_to_remove = []
    for setup_idx, setup in enumerate(build_data.get("skill_setups", [])):
        gems_to_remove = []
        for gem in setup.get("gems", []):
            gem_name = gem.get("name", "")
            gem_key = gem_name.lower().strip()

            # Check for "Awakened" prefix or other removed gems
            if gem_key in REMOVED_GEM_REPLACEMENTS:
                replacement = REMOVED_GEM_REPLACEMENTS[gem_key]
                fixes.append({
                    "type": "gem",
                    "severity": "fixed",
                    "message": f"Replaced '{gem_name}' with '{replacement}' (removed from PoE 1)",
                })
                gem["name"] = replacement
            elif gem_key in REMOVED_GEMS:
                # No direct replacement — remove the gem
                gems_to_remove.append(gem)
                fixes.append({
                    "type": "gem",
                    "severity": "fixed",
                    "message": f"Removed '{gem_name}' — removed from PoE 1",
                })
            elif gem_key.startswith("awakened "):
                # Catch any Awakened gem we might have missed in the explicit list
                base_name = gem_name.replace("Awakened ", "").strip()
                # Ensure " Support" suffix
                if not base_name.lower().endswith(" support"):
                    base_name += " Support"
                fixes.append({
                    "type": "gem",
                    "severity": "fixed",
                    "message": f"Replaced '{gem_name}' with '{base_name}' (Awakened gems removed from PoE 1)",
                })
                gem["name"] = base_name
            elif not gem.get("is_support") and gem_key not in gem_db.all_gem_names:
                # Active skill doesn't exist in POB data at all — hallucinated name
                gems_to_remove.append(gem)
                fixes.append({
                    "type": "gem",
                    "severity": "fixed",
                    "message": f"Removed '{gem_name}' — not a real PoE 1 active skill",
                })

        # Remove individual gems flagged for removal
        for gem in gems_to_remove:
            if gem in setup.get("gems", []):
                setup["gems"].remove(gem)

        # If setup has no active gems left (only supports), remove the entire setup
        remaining_actives = [g for g in setup.get("gems", []) if not g.get("is_support")]
        if not remaining_actives and setup.get("gems", []):
            setup_label = setup.get("label", setup.get("slot", f"Setup {setup_idx+1}"))
            sup_names = [g["name"] for g in setup.get("gems", [])]
            fixes.append({
                "type": "gem",
                "severity": "fixed",
                "message": f"Removed orphaned supports from '{setup_label}' ({', '.join(sup_names)}) — no active skill remains",
            })
            setups_to_remove.append(setup_idx)

    # Remove entire setups flagged for removal (reverse order)
    setups = build_data.get("skill_setups", [])
    for i in sorted(setups_to_remove, reverse=True):
        setups.pop(i)

    # ── Fix gem setups: replace incompatible supports ────────────────────
    for setup in build_data.get("skill_setups", []):
        gems = setup.get("gems", [])

        active_gems = [g for g in gems if not g.get("is_support")]
        support_gems = [g for g in gems if g.get("is_support")]

        # Collect gems to fix: (gem_dict, reason_string)
        gems_to_fix: list[tuple[dict, str]] = []

        for support in support_gems:
            support_name = support.get("name", "")

            # A support is valid if it can support AT LEAST ONE active in the group.
            if active_gems:
                can_support_any = False
                last_reason = ""
                last_active = ""
                for active in active_gems:
                    active_name = active.get("name", "")
                    compatible, reason = gem_db.can_support(active_name, support_name)
                    if compatible:
                        can_support_any = True
                        break
                    last_reason = reason
                    last_active = active_name

                if not can_support_any:
                    gems_to_fix.append((support, f"cannot support '{last_active}' ({last_reason})"))
                    continue

            # Check keystone conflicts
            for ks_name, rules in KEYSTONE_CONFLICTS.items():
                if ks_name not in keystones:
                    continue
                for bad in rules.get("bad_gems", []):
                    if bad in support_name.lower():
                        gems_to_fix.append((support, f"conflicts with {ks_name.title()}"))
                        break

        # Also check active gems for keystone conflicts
        for active in active_gems:
            active_name = active.get("name", "")
            for ks_name, rules in KEYSTONE_CONFLICTS.items():
                if ks_name not in keystones:
                    continue
                for bad in rules.get("bad_gems", []):
                    if bad in active_name.lower():
                        gems_to_fix.append((active, f"conflicts with {ks_name.title()}"))
                        break

        # Now replace or remove each bad gem
        if gems_to_fix and active_gems:
            current_names = {g.get("name", "").lower() for g in gems}
            primary_active = active_gems[0]
            primary_active_name = primary_active.get("name", "")

            for gem, reason in gems_to_fix:
                if gem not in gems:
                    continue
                old_name = gem["name"]

                if gem.get("is_support"):
                    # Try to find a replacement
                    replacements = gem_db.find_replacement_supports(
                        primary_active_name,
                        existing_gems=current_names,
                        damage_type=build_data.get("damage_type", ""),
                        weapon_type=build_data.get("weapon_type", ""),
                        keystones=keystones,
                        count=1,
                    )
                    if replacements:
                        replacement = replacements[0]
                        gem["name"] = replacement
                        gem["level"] = 20
                        gem["quality"] = 20
                        current_names.add(replacement.lower())
                        current_names.discard(old_name.lower())
                        fixes.append({
                            "type": "gem",
                            "severity": "fixed",
                            "message": f"Replaced '{old_name}' with '{replacement}' — {reason}",
                        })
                    else:
                        gems.remove(gem)
                        current_names.discard(old_name.lower())
                        fixes.append({
                            "type": "gem",
                            "severity": "fixed",
                            "message": f"Removed '{old_name}' — {reason} (no valid replacement found)",
                        })
                else:
                    gems.remove(gem)
                    current_names.discard(old_name.lower())
                    fixes.append({
                        "type": "gem",
                        "severity": "fixed",
                        "message": f"Removed '{old_name}' — {reason}",
                    })

    # ── Fix jewel mods: cap values and remove keystone-conflicting mods ──
    for idx, jewel in enumerate(build_data.get("jewels", [])):
        fixed_mods = []
        for mod in jewel.get("mods", []):
            # Cap values
            valid, suggestion = validate_jewel_mod(mod)
            if not valid:
                fixes.append({
                    "type": "jewel",
                    "severity": "fixed",
                    "message": f"Jewel '{jewel.get('name', idx+1)}': capped {mod} → {fix_jewel_mod(mod)}",
                })
                mod = fix_jewel_mod(mod)

            # Check keystone conflicts
            should_remove = False
            for ks_name, rules in KEYSTONE_CONFLICTS.items():
                if ks_name not in keystones:
                    continue
                for bad_mod in rules.get("bad_jewel_mods", []):
                    if bad_mod in mod.lower():
                        should_remove = True
                        fixes.append({
                            "type": "jewel",
                            "severity": "fixed",
                            "message": f"Removed jewel mod '{mod}' — conflicts with {ks_name.title()}",
                        })
                        break
                if should_remove:
                    break

            if not should_remove:
                fixed_mods.append(mod)

        jewel["mods"] = fixed_mods

    return fixes


# ── Module-level singleton ────────────────────────────────────────────────────
_gem_db: GemDatabase | None = None


def get_gem_db() -> GemDatabase:
    """Get or create the singleton GemDatabase."""
    global _gem_db
    if _gem_db is None:
        _gem_db = GemDatabase()
        _gem_db.load()
    return _gem_db
