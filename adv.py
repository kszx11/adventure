# Written by Kimball Springall
# Standard libs
import os
import sys
import time
import json
import random
# On Windows, enable ANSI escape handling via colorama
try:
    import colorama
    colorama.init(convert=True)
except ImportError:
    pass

# Utility: normalize multiline text (strip extra CRs and collapse multiple blanks)
def normalize_text(text):
    # remove CR
    text = text.replace("\r", "")
    lines = text.split("\n")
    # strip leading/trailing blanks
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    # collapse multiple blanks
    out = []
    blank = False
    for line in lines:
        if not line.strip():
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False
    return "\n".join(out)

# Configuration flags
prune_enabled = True
# Determine if prompt_toolkit is available for advanced input/completion
_pt_enabled = True
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
except ImportError:
    print("[Warning] prompt_toolkit not installed; falling back to basic input().", file=sys.stderr)
    _pt_enabled = False
from openai import OpenAI

# ----------------------------------------------------------------
# CONFIGURATION & CLIENT INIT
# ----------------------------------------------------------------

client = OpenAI()
client.api_key = os.getenv("OPENAI_API_KEY")
if not client.api_key:
    print("[Error] OPENAI_API_KEY environment variable not set.", file=sys.stderr)
    sys.exit(1)
MODEL_NAME = "gpt-4.1-mini"  #"gpt-3.5-turbo"

# Enforce naming and backstory in every NPC description
SYSTEM_PROMPT = """
You are Realmweaver, the narrator and engine of an immersive, open‐ended text adventure.
Whenever you describe people in a scene, ALWAYS give them:
  1) A full name and title/role (e.g. "Selene the Librarian").
  2) A brief backstory snippet—one or two sentences about their past, interests, or beliefs.

The world is full of towns, villages, cities, forests (deep and enchanted),
mystic ruins, markets, taverns, temples, parks, homes, and travelers.

Keep track of the player's location, the NPCs you've introduced (with consistent names),
and ensure continuity as they walk, examine, or speak with people and creatures.
When the player types commands like "go to…", "examine…", or "talk to X",
respond with a vivid, immersive description or dialogue.
"""
# There are no quests—only exploration, conversation, and discovery.

# ----------------------------------------------------------------
# GLOBAL NPC STORAGE
# ----------------------------------------------------------------

# Stores npc_name -> {"bio": "...", "backstory": "...", "affinity": int}
npc_data = {}

# Scene descriptions: location -> last LLM description
scene_descriptions = {}

# Player state tracking
player_state = {
    "stats": {},
    "inventory": [],
    "journal": [],
    "visited_locations": [],
    "map_graph": {},  # location -> set of neighbors
    "current_location": None,
}

# Items database: item_name -> description
items_data = {}

# ANSI colors
RED = "\033[1;31m"
GREEN = "\033[1;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[1;34m"
RESET = "\033[0m"

# Base commands for tab completion
BASE_COMMANDS = [
    "go to", "move to", "north", "south", "east", "west",
    "look", "examine", "talk to", "inventory", "stats", "journal",
    "save", "load", "map", "hint", "roll"
]
# DummySession fallback for input()/prompt
class DummySession:
    def prompt(self, prompt_text):
        return input(prompt_text)

if _pt_enabled:
    class AdventureCompleter(Completer):
        """Dynamic tab-completion for commands, NPCs, items, and locations."""
        def get_completions(self, document, complete_event):
            word = document.get_word_before_cursor()
            options = (BASE_COMMANDS +
                       list(npc_data.keys()) +
                       player_state.get("inventory", []) +
                       player_state.get("visited_locations", []) +
                       list(items_data.keys()))
            for opt in options:
                if opt.startswith(word):
                    yield Completion(opt, start_position=-len(word))
    try:
        session = PromptSession(completer=AdventureCompleter())
    except Exception as e:
        print(f"[Warning] prompt_toolkit session failed ({e}); falling back to basic input()", file=sys.stderr)
        session = DummySession()
else:
    session = DummySession()
# Unified prompt: wrap session.prompt to fall back to basic input on any prompt-toolkit errors
def prompt_user(prompt_text):
    """Prompt user; use prompt_toolkit if available, else built-in input()."""
    if _pt_enabled:
        try:
            return session.prompt(prompt_text)
        except Exception:
            return input(prompt_text)
    return input(prompt_text)

# Save file path
SAVE_FILE = "savegame.json"

# Initialize player stats and state for a new game
def init_player_state():
    stats = {s: random.randint(8, 18) for s in ("STR", "DEX", "CON", "INT", "WIS", "CHA")}
    player_state.update({
        "stats": stats,
        "inventory": [],
        "journal": [],
        "visited_locations": [],
        "map_graph": {},
        "current_location": None,
    })

# Save current game to SAVE_FILE
def save_game(history):
    data = {
        "npc_data": npc_data,
        "player_state": player_state,
        "history": history,
    }
    with open(SAVE_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"{YELLOW}Game saved to {SAVE_FILE}.{RESET}")

# Load game from SAVE_FILE; returns history or None
def load_game():
    if not os.path.isfile(SAVE_FILE):
        print(f"{RED}No save file found.{RESET}")
        return None
    with open(SAVE_FILE, "r") as f:
        data = json.load(f)
    npc_data.clear()
    npc_data.update(data.get("npc_data", {}))
    player_state.update(data.get("player_state", {}))
    print(f"{YELLOW}Game loaded from {SAVE_FILE}.{RESET}")
    return data.get("history", [])

# Prune and summarize long history to control token usage
def prune_history(history, max_msgs=30, keep_tail=10):
    if len(history) <= max_msgs:
        return history
    to_summarize = history[:-keep_tail]
    prompt = [{"role": "system", "content": "Summarize the following adventure context in two sentences."}]
    prompt.extend(to_summarize)
    summary = call_openai(prompt)
    new_history = [{"role": "system", "content": f"SUMMARY: {summary}"}] + history[-keep_tail:]
    print(f"{YELLOW}[History pruned and summarized]{RESET}")
    return new_history

# List items in scene via OpenAI
def list_items(history):
    prompt = history + [{
        "role": "user",
        "content": "List, in a comma-separated list, all objects present in this scene. If none, reply 'None'."
    }]
    raw = call_openai(prompt)
    items = []
    for entry in raw.split(","):
        name = entry.strip().strip(".!?:;")
        if name and name.lower() != "none":
            items.append(name)
    return items

# List exits in scene via OpenAI
def list_exits(history):
    prompt = history + [{
        "role": "user",
        "content": "List, in a comma-separated list, all exits or directions available from this scene. If none, reply 'None'."
    }]
    raw = call_openai(prompt)
    exits = []
    for entry in raw.split(","):
        name = entry.strip().strip(".!?:;")
        if name and name.lower() != "none":
            exits.append(name)
    return exits

# Print summary of the environment: exits, NPCs, items
def print_environment_summary(history):
    npcs = list_npcs(history)
    items = list_items(history)
    exits = list_exits(history)
    print(f"{BLUE}Exits:{RESET} {', '.join(exits) or 'None'}")
    print(f"{GREEN}NPCs here:{RESET} {', '.join(npcs) or 'None'}")
    print(f"{YELLOW}Items here:{RESET} {', '.join(items) or 'None'}")

# ----------------------------------------------------------------
# HELPER FUNCTIONS
# ----------------------------------------------------------------

def call_openai(messages):
    """Call the OpenAI API with retries."""
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.8,
                max_tokens=500,
                top_p=0.9,
            )
            # Normalize line endings (remove carriage returns) and strip
            text = resp.choices[0].message.content.replace("\r", "")
            return text.strip()
        except Exception as e:
            print(f"[Warning] API error: {e}. Retrying…", file=sys.stderr)
            time.sleep(1)
    # On repeated failure, return a placeholder rather than exiting
    print("[Error] Could not reach OpenAI API. Continuing with placeholder response.", file=sys.stderr)
    return "[The realm is silent; no response comes.]"

def print_help():
    print("""
Available commands:
  go to / travel to <location> - Move to a named place (or directions).
  examine/look at <obj>   - Inspect people, objects, or scenery.
  talk to <NPC name>      - Begin a conversation with someone here.
  talk to                 - List NPCs in current scene.
  look/observe/where      - Re-describe your surroundings and summary.
  inventory               - Show your items.
  stats                   - Show your character stats.
  journal                 - Show your journal entries.
  save                    - Save your current game.
  load                    - Load a saved game.
  map [<location>]        - Show ASCII map and details for a location (default=current).
  hint                    - Get an in-game hint.
  set prune on|off        - Enable or disable automatic history summarization.
  roll <STAT> [DC]        - Perform a d20 skill/attribute check.
  help                    - Show this help text.
  quit/exit/stop          - End the adventure or exit an NPC chat.
""")

def list_npcs(history):
    """Ask Realmweaver to list current NPC names in the scene."""
    prompt = history + [
        {"role":"user", "content":
            "List, in a comma-separated list, the FULL NAMES of all NPCs currently "
            "present in this scene. If none, reply 'None'."}
    ]
    raw = call_openai(prompt)
    # Parse comma-separated names, stripping punctuation, ignore 'none'
    npc_list = []
    for entry in raw.split(','):
        name = entry.strip().strip(".!?:;")
        if name and name.lower() != "none":
            npc_list.append(name)
    return npc_list

def start_conversation(npc_name, history):
    """
    Start a first-person back-and-forth with npc_name.
    On first encounter, fetch and store their bio + backstory.
    """
    # If we don't have this NPC's data yet, prompt for it
    if npc_name not in npc_data:
        # Ask for bio and backstory in one shot
        summary_prompt = history[-6:] + [
            {"role":"user", "content":
                f"You previously described an NPC named '{npc_name}'.\n"
                "Please provide TWO clearly labeled sections:\n"
                "BIO: One sentence describing who they are (name/title/role).\n"
                "BACKSTORY: Two sentences about their past, interests, or beliefs.\n"
                "Respond exactly in this format."}
        ]
        summary = call_openai(summary_prompt)
        # Parse the two sections
        bio, backstory = "", ""
        for line in summary.splitlines():
            if line.upper().startswith("BIO:"):
                bio = line[len("BIO:"):].strip()
            if line.upper().startswith("BACKSTORY:"):
                backstory = line[len("BACKSTORY:"):].strip()
        # Fallback if parsing failed
        if not bio: bio = f"{npc_name}, a person of note."
        if not backstory: backstory = "They prefer to keep much of their past private."
        npc_data[npc_name] = {"bio": bio, "backstory": backstory}

    # Build a strict system prompt for the NPC chat
    info = npc_data[npc_name]
    npc_sys = (
        f"You are {npc_name}.\n"
        f"{info['bio']}\n"
        f"Backstory: {info['backstory']}\n\n"
        "Speak in first-person as yourself. ALWAYS refer to yourself by that exact name. "
        "The player may ask about your past, interests, or beliefs—answer accordingly. "
        "When the player says 'goodbye', 'exit', or 'bye', end the conversation politely."
    )
    conv = [{"role":"system", "content": npc_sys}]

    print(f"\n{BLUE}— You begin talking with {npc_name}. (type 'goodbye' to end) —{RESET}\n")
    while True:
        line = prompt_user("You: ").strip()
        if not line:
            continue
        if line.lower() in ("goodbye","exit","bye"):
            conv.append({"role":"user","content":line})
            farewell = call_openai(conv)
            print(f"{GREEN}{npc_name}:{RESET} {farewell}\n")
            # update affinity
            npc_data[npc_name].setdefault("affinity", 0)
            npc_data[npc_name]["affinity"] += 1
            print(f"— Conversation ended. You return to exploration. —\n")
            return
        conv.append({"role":"user","content":line})
        reply = call_openai(conv)
        print(f"{GREEN}{npc_name}:{RESET} {reply}")
        conv.append({"role":"assistant","content":reply})

# ----------------------------------------------------------------
# MAIN GAME LOOP
# ----------------------------------------------------------------

def main():
    global prune_enabled  # allow toggling summarization on/off
    # Game menu: New or Load
    print(f"{BLUE}Welcome to the Immersive Text Adventure!{RESET}")
    # Main menu prompt
    print("1) New game  2) Load game  3) Quit")
    choice = prompt_user("> ").strip()
    if choice == "2":
        loaded = load_game()
        if loaded is None:
            init_player_state()
        history = loaded or []
        if history:
            last = history[-1]
            if last["role"] == "assistant":
                print(f"{BLUE}{last['content']}{RESET}")
    elif choice == "3":
        print(f"{YELLOW}Goodbye!{RESET}")
        return
    else:
        init_player_state()
        # Ask starting context
        print("First, choose when and where your story begins (e.g. Year 1372, Isle of Everdawn)")
        start_ctx = prompt_user("> ").strip()
        if not start_ctx:
            start_ctx = "Year 1372, in the misty Isle of Everdawn"
        print(f"\n{BLUE}…Very well. Setting the scene…{RESET}\n")
        history = [
            {"role":"system",  "content":SYSTEM_PROMPT},
            {"role":"user",    "content":f"Begin the adventure: {start_ctx}."}
        ]
        intro = call_openai(history)
        intro = normalize_text(intro)
        print(f"{BLUE}{intro}{RESET}")
        history.append({"role":"assistant","content":intro})
        # initialize first scene description and location
        player_state["current_location"] = start_ctx
        scene_descriptions[start_ctx] = intro
        if start_ctx not in player_state["visited_locations"]:
            player_state["visited_locations"].append(start_ctx)
        print_help()

    while True:
        # get next command (with current location prompt)
        loc = player_state.get("current_location") or ""
        prompt_str = f"{loc}> " if loc else "> "
        cmd = prompt_user(prompt_str).strip()
        if not cmd:
            continue
        lc = cmd.lower()
        # allow toggling history pruning
        if lc.startswith("set prune"):
            parts = lc.split()
            if len(parts) >= 3 and parts[2] in ("on","off"):
                prune_enabled = (parts[2] == "on")
                state = "enabled" if prune_enabled else "disabled"
                print(f"History summarization {state}.")
            else:
                print("Usage: set prune on|off")
            continue
        # prune history if enabled
        if prune_enabled:
            history = prune_history(history)

        # Exit
        if lc in ("quit","exit","stop"):
            print(f"{YELLOW}Farewell, traveler!{RESET}")
            break
        # Help
        if lc in ("help","?"):
            print_help()
            continue
        # Inventory
        if lc == "inventory":
            print(f"{YELLOW}Inventory:{RESET} {', '.join(player_state['inventory']) or 'Empty'}")
            continue
        # Stats
        if lc == "stats":
            for k,v in player_state["stats"].items():
                print(f" {k}: {v}")
            continue
        # Journal
        if lc == "journal":
            print(f"{BLUE}Journal Entries:{RESET}")
            for entry in player_state["journal"]:
                print(f" - {entry}")
            continue
        # Save
        if lc == "save":
            save_game(history)
            continue
        # Load
        if lc == "load":
            loaded = load_game()
            if loaded:
                history = loaded
            continue
        # Map command: ascii-tree around a location or current
        if lc.startswith("map"):
            parts = cmd.split(maxsplit=1)
            if len(parts) == 1 or not parts[1].strip():
                target = player_state.get("current_location")
            else:
                # normalize target to title-case to match map_graph keys
                target = parts[1].strip().title()
            graph = player_state.get("map_graph", {})
            visits = player_state.get("visited_locations", [])
            print(f"{BLUE}Map for '{target}':{RESET}")
            # list all visited locations
            if visits:
                print(f"{YELLOW}Visited:{RESET} {', '.join(visits)}")
            else:
                print(f"{YELLOW}No visited locations yet.{RESET}")
            # draw connections from target
            desc = scene_descriptions.get(target)
            if target not in graph:
                print(f"{YELLOW}No map connections for '{target}'.{RESET}")
                # show stored description if available
                if desc:
                    print(f"\n{GREEN}Details for '{target}':{RESET}")
                    for line in desc.splitlines():
                        print(f"  {line}")
                continue
            # recursive ASCII tree with cycle guard
            def draw(node, parent=None, prefix="", is_last=True, visited=None):
                # initialize or update visited set
                if visited is None:
                    visited = {node}
                    print(f"{prefix}{node}")
                else:
                    branch = "└─ " if is_last else "├─ "
                    print(f"{prefix}{branch}{node}")
                    visited.add(node)
                # children are neighbors except parent and already visited
                children = sorted(graph.get(node, []))
                if parent in children:
                    children.remove(parent)
                for idx, child in enumerate(children):
                    if child in visited:
                        continue
                    is_last_child = (idx == len(children) - 1)
                    new_prefix = prefix + ("   " if is_last else "│  ")
                    draw(child, node, new_prefix, is_last_child, visited)
            draw(target)
            # show stored scene description if available
            if desc:
                print(f"\n{GREEN}Details for '{target}':{RESET}")
                for line in desc.splitlines():
                    print(f"  {line}")
            continue
        # Hint
        if lc == "hint":
            loc = player_state.get("current_location","")
            hint_prompt = history + [{"role":"user","content":f"I'm stuck at {loc}. Please give me a hint."}]
            hint = call_openai(hint_prompt)
            hint = normalize_text(hint)
            print(f"{YELLOW}Hint:{RESET} {hint}")
            continue
        # Roll dice: roll <stat> [DC]
        if lc.startswith("roll"):
            parts = cmd.split()
            if len(parts)>=2:
                stat = parts[1].upper()
                if stat in player_state["stats"]:
                    mod = (player_state["stats"][stat]-10)//2
                    roll = random.randint(1,20) + mod
                    result = f"Rolled 1d20 + {mod} = {roll}"
                    if len(parts)>=3 and parts[2].isdigit():
                        dc = int(parts[2])
                        success = "Success" if roll>=dc else "Failure"
                        result += f" vs DC {dc}: {success}"
                    print(f"{YELLOW}{result}{RESET}")
                else:
                    print(f"{RED}Unknown stat '{stat}'.{RESET}")
            else:
                print("Usage: roll <stat> [DC]")
            continue

        # TALK TO with no name: list NPCs here
        if lc == "talk to":
            npcs = list_npcs(history)
            if not npcs:
                print(f"{YELLOW}There's no one here to talk to.{RESET}")
            else:
                print(f"{GREEN}You can talk to:{RESET} {', '.join(npcs)}")
            continue

        # TALK TO with a name
        if lc.startswith("talk to "):
            npc_name = cmd[8:].strip()
            if not npc_name:
                print("Usage: talk to <full NPC name>")
                continue
            start_conversation(npc_name, history)
            continue

        # Describe surroundings
        if lc in ("look","observe","where"):
            history.append({"role":"user","content":cmd})
            desc = call_openai(history)
            desc = normalize_text(desc)
            print(f"\n{BLUE}{desc}{RESET}")
            history.append({"role":"assistant","content":desc})
            print_environment_summary(history)
            continue
        # Examine/look commands
        if any(lc.startswith(w) for w in ("examine ","look at ","inspect ")):
            parts = cmd.split(maxsplit=1)
            if len(parts)>=2:
                target = parts[1]
                history.append({"role":"user","content":cmd})
                desc = call_openai(history)
                desc = normalize_text(desc)
                print(f"{BLUE}{desc}{RESET}")
                items_data[target] = desc
                player_state["journal"].append(f"Examined {target}.")
                history.append({"role":"assistant","content":desc})
            else:
                print("Usage: examine <object>")
            continue
        # Movement commands
        moved = False
        for prefix in ("go to ", "move to ", "travel to "):
            if lc.startswith(prefix):
                dest = cmd[len(prefix):].strip()
                # normalize location names (title case)
                dest = dest.title()
                moved = True
                break
        if not moved and lc in ("north","south","east","west"):
            dest = lc
            # normalize direction names
            dest = dest.title()
            moved = True
        if moved:
            prev = player_state.get("current_location")
            if prev:
                player_state["map_graph"].setdefault(prev, set()).add(dest)
                player_state["map_graph"].setdefault(dest, set()).add(prev)
            # move player and record visit
            player_state["current_location"] = dest
            if dest not in player_state["visited_locations"]:
                player_state["visited_locations"].append(dest)
            history.append({"role":"user","content":cmd})
            resp = call_openai(history)
            resp = normalize_text(resp)
            print(f"\n{BLUE}{resp}{RESET}")
            history.append({"role":"assistant","content":resp})
            # store scene description for this location
            scene_descriptions[dest] = resp
            print_environment_summary(history)
            continue
        # All other: forward to Realmweaver
        history.append({"role":"user","content":cmd})
        resp = call_openai(history)
        resp = normalize_text(resp)
        print(f"\n{BLUE}{resp}{RESET}")
        history.append({"role":"assistant","content":resp})

if __name__ == "__main__":
    main()
