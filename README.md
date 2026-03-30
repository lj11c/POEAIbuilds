# POE AI Build Generator

An AI-powered Path of Exile 1 build generator that creates complete, importable Path of Building (POB) builds from natural language descriptions.

Describe the kind of build you want (e.g. *"tanky two-handed sword Slayer for mapping"*) and the tool generates:

- A fully allocated passive tree with connected pathing
- Skill gem setups with support gems and levels
- Custom jewels socketed into the tree
- Gear recommendations and a leveling guide
- A POB import code you can paste directly into Path of Building

## How It Works

1. **You describe a build** in plain English via the web UI
2. **Claude AI** (Anthropic) generates a coherent build plan -- class, passives, gems, gear
3. **The pathfinder engine** parses the actual 3.28 passive tree graph and computes a valid connected path through your chosen notables using a greedy Steiner tree algorithm
4. **Conflict filtering** automatically avoids pathing through small passives that contradict your build (e.g. shield nodes on a two-handed build)
5. **The result** is a POB import code with allocated nodes, socketed jewels, gem setups, and a full build guide in the Notes tab

## Prerequisites

- **Python 3.10+** -- [Download here](https://www.python.org/downloads/) (check "Add Python to PATH" during install)
- **An Anthropic API key** -- [Get one here](https://console.anthropic.com/settings/keys) (requires adding credits at [billing settings](https://console.anthropic.com/settings/billing))
- **Path of Building Community** -- [Download here](https://pathofbuilding.community/) (to import the generated builds)

> **Note:** The Anthropic API is pay-per-use and separate from a Claude Pro subscription. You'll need to add credits to your API account. Each build generation costs roughly $0.01-0.03.

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/lj11c/POEAIbuilds.git
cd POEAIbuilds
```

### 2. Set up the POB data files

The generator needs Path of Building Community's data files (passive tree, gem database) to function. Clone the POB Community repo into the `POB` folder:

```bash
mkdir POB
cd POB
git clone https://github.com/PathOfBuildingCommunity/PathOfBuilding.git PathOfBuilding-dev
cd ..
```

The tool reads from:
- `POB/PathOfBuilding-dev/src/TreeData/3_28/tree.lua` (passive tree graph)
- `POB/PathOfBuilding-dev/src/Data/Gems.lua` (gem database)

### 3. Add your API key

Copy the example env file and add your Anthropic API key:

```bash
cp .env.example .env
```

Then open `.env` in a text editor and replace `your-api-key-here` with your actual API key:

```
ANTHROPIC_API_KEY=sk-ant-api03-xxxxx...
```

You can get an API key from [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys).

### 4. Install dependencies and run

**On Windows** (easiest):

Double-click `start.bat` -- it installs dependencies and starts the server automatically.

**On any platform** (manual):

```bash
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

### 5. Open the web UI

Navigate to **http://localhost:8000** in your browser.

Type a build description (e.g. *"lightning arrow Deadeye, fast mapper, league start friendly"*) and click **Generate Build**.

### 6. Import into Path of Building

1. Copy the import code from the **POB Import** tab
2. Open Path of Building Community
3. Click **Import/Export Build** > **Import from website** (or Ctrl+V)
4. Paste the code and click **Import**

## Project Structure

```
poe-ai-build-generator/
  app.py                 # FastAPI backend + Claude API integration
  pob_utils.py           # POB XML generation and import code encoding
  tree_pathfinder.py     # Passive tree graph parser and pathfinding engine
  data_parser.py         # Gem and tree data parsers
  requirements.txt       # Python dependencies
  start.bat              # Windows quick-start script
  .env.example           # API key template
  static/
    index.html           # Web UI
    style.css            # PoE-themed dark UI
    app.js               # Frontend logic
  POB/
    PathOfBuilding-dev/  # POB Community source (cloned separately)
```

## Features

- **Natural language input** -- describe builds however you want
- **Smart passive tree pathing** -- greedy Steiner tree algorithm computes the shortest connected path through your target notables
- **Weapon/style conflict filtering** -- automatically avoids pathing through nodes that conflict with your build (e.g. won't take shield block nodes on a two-handed build)
- **Gem ID resolution** -- maps gem names to POB's internal IDs for accurate imports
- **Custom jewels** -- generates and sockets rare jewels with build-appropriate stats
- **Complete build guide** -- leveling progression, gear recommendations, playstyle notes all embedded in the POB Notes tab
- **PoE-themed UI** -- dark theme with gold accents inspired by the game

## Configuration

| Environment Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key (required) |

The Claude model can be changed by editing `CLAUDE_MODEL` in `app.py` (default: `claude-sonnet-4-6`).

## Troubleshooting

**"ANTHROPIC_API_KEY not set"**
Make sure you copied `.env.example` to `.env` and added your key. The file must be named exactly `.env` (not `.env.txt`).

**"Credit balance is too low"**
The Anthropic API requires prepaid credits, separate from a Claude Pro subscription. Add credits at [console.anthropic.com/settings/billing](https://console.anthropic.com/settings/billing).

**"Python not found"**
Install Python from [python.org](https://www.python.org/downloads/) and make sure to check **"Add Python to PATH"** during installation. Restart your terminal after installing.

**POB import error about missing URL element**
This was a known bug that has been fixed. If you see it, make sure you have the latest version of the code.

**No nodes on the passive tree**
Make sure the POB data files are in the correct location: `POB/PathOfBuilding-dev/src/TreeData/3_28/tree.lua` must exist.

## License

MIT License. See [LICENSE](LICENSE) for details.

This project uses data from [Path of Building Community](https://github.com/PathOfBuildingCommunity/PathOfBuilding), which is licensed under its own terms.

Path of Exile is a registered trademark of Grinding Gear Games. This project is not affiliated with or endorsed by Grinding Gear Games.
