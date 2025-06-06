# Written by Kimball Springall
"""
advmp.py -- MicroPython text adventure on Raspberry Pi Pico W
Port of adv.py: immersive exploration and NPC chat via OpenAI Chat API
Requires: MicroPython build with network, urequests, ujson
"""
import network
import time
import ujson
import urequests
import sys
import wifi
import config

# ----------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------
settings = config.read_config_settings()

API_KEY = settings["api_key"]
MODEL_NAME = settings["model_name"]
# print("Model:", MODEL_NAME)
# print("API key:", API_KEY)
# sys.exit(0)

wlan = wifi.connect_wifi()
if not wlan:
    print("Failed to connect to Wi-Fi.")
    sys.exit(1)

# MODEL_NAME        = MODEL_NAME
TEMPERATURE       = 0.8
TOP_P             = 0.9
MAX_TOKENS        = 200
HISTORY_LIMIT     = 6

# System prompt
SYSTEM_PROMPT = (
    "You are Realmweaver, the narrator and engine of an immersive, open-ended text adventure. "
    "Whenever you describe people in a scene, ALWAYS give them: "
    "1) A full name and title/role. 2) A brief backstory snippet. "
    "The world is full of towns, villages, forests, cities, ruins, markets, taverns, temples, homes, travelers. "
    "Keep track of the player's location and NPCs. "
    "When the player says 'goodbye', end an NPC conversation politely."
)

# ----------------------------------------------------------------
# UTILITY
# ----------------------------------------------------------------
def load_api_key(fname):
    try:
        with open(fname) as f:
            return f.read().strip()
    except:
        print("Error: cannot read API key file.")
        sys.exit(1)

def wifi_connect(ssid, pwd, timeout=15):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(ssid, pwd)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > timeout*1000:
                print("Wi-Fi connect failed")
                return False
            time.sleep(1)
    # print("Wi-Fi IP", wlan.ifconfig()[0])
    return True

def call_openai(api_key, history):
    # trim history
    if len(history) > HISTORY_LIMIT:
        history = [history[0]] + history[-(HISTORY_LIMIT-1):]
    url = "https://api.openai.com/v1/chat/completions"
    hdr = {"Content-Type":"application/json", "Authorization":f"Bearer {api_key}"}
    body = {"model":MODEL_NAME, "messages":history,
            "temperature":TEMPERATURE, "top_p":TOP_P,
            "max_tokens":MAX_TOKENS}
    try:
        r = urequests.post(url, headers=hdr, data=ujson.dumps(body))
    except Exception as e:
        print("API error", e)
        return None
    if r.status_code != 200:
        print("HTTP", r.status_code, r.text)
        r.close()
        return None
    j = r.json(); r.close()
    try:
        return j['choices'][0]['message']['content'].strip()
    except:
        return None

def print_help():
    print("Commands:")
    print("  go to <loc>        - Move to a place")
    print("  examine <obj>      - Inspect people or objects")
    print("  talk to <name>     - Chat with an NPC")
    print("  talk to            - List NPCs here")
    print("  look               - Describe surroundings")
    print("  help               - Show this help")
    print("  quit               - Exit game")

# ----------------------------------------------------------------
# MAIN LOOP
# ----------------------------------------------------------------
def main():
    # init history
    history = [{"role":"system","content":SYSTEM_PROMPT}]
    # starting context
    print("\nWelcome to the Pico Text Adventure Game!")
    print("Setting up the world...")
    # print("Where and when do you start?")
    # start = input("> ").strip()
    # if not start:
    start = "Year 1372, Isle of Everdawn"
    history.append({"role":"user","content":f"Begin the adventure: {start}."})
    intro = call_openai(API_KEY, history)
    if not intro:
        print("Failed to set up world. This is probably a network error or unable to get data from OpenAI.")
        return
    print("\n"+intro)
    history.append({"role":"assistant","content":intro})
    print_help()
    # game REPL
    while True:
        cmd = input("\n> ").strip()
        lc = cmd.lower()
        if not cmd:
            continue
        if lc in ("quit","exit"):
            print("Farewell, traveler!")
            break
        if lc == "help":
            print_help(); continue
        # list NPCs
        if lc == "talk to":
            # ask AI for NPC list
            prompt = history + [{"role":"user","content":
                "List NPCs in this scene (comma-separated) or 'None'."}]
            names = call_openai(API_KEY, prompt)
            print(names or "None")
            continue
        # conversation with NPC
        if lc.startswith("talk to "):
            npc = cmd[8:].strip()
            # system for NPC chat
            sys = f"You are {npc}. Speak in first-person as yourself."
            conv = [{"role":"system","content":sys}]
            print(f"Talking to {npc}. (goodbye to exit)")
            while True:
                line = input("You: ").strip()
                if line.lower() in ("goodbye","exit","bye"):
                    break
                conv.append({"role":"user","content":line})
                rep = call_openai(API_KEY, conv)
                print(f"{npc}: {rep}")
                conv.append({"role":"assistant","content":rep})
            continue
        # general commands forwarded
        history.append({"role":"user","content":cmd})
        reply = call_openai(API_KEY, history)
        if not reply:
            print("No response. Try again.")
            continue
        print("\n"+reply)
        history.append({"role":"assistant","content":reply})

if __name__ == "__main__":
    main()

