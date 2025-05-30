package main

// go mod init adv
// go build -o adv.exe
import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io/ioutil"
	"math/rand"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
)

// ANSI color codes
const (
	Red    = "\033[1;31m"
	Green  = "\033[1;32m"
	Yellow = "\033[1;33m"
	Blue   = "\033[1;34m"
	Reset  = "\033[0m"
	// System prompt enforcing naming/backstory rules
	SYSTEM_PROMPT = `You are Realmweaver, the narrator and engine of an immersive, open‐ended text adventure.
Whenever you describe people in a scene, ALWAYS give them:
  1) A full name and title/role (e.g. "Selene the Librarian").
  2) A brief backstory snippet—one or two sentences about their past, interests, or beliefs.

The world is full of towns, villages, cities, forests (deep and enchanted),
mystic ruins, markets, taverns, temples, parks, homes, and travelers.

Keep track of the player's location, the NPCs you've introduced (with consistent names),
and ensure continuity as they walk, examine, or speak with people and creatures.
When the player types commands like "go to…", "examine…", or "talk to X",
respond with a vivid, immersive description or dialogue.
` // There are no quests—only exploration, conversation, and discovery.
)

// Message for OpenAI chat API
type Message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// ChatRequest payload
type ChatRequest struct {
	Model       string    `json:"model"`
	Messages    []Message `json:"messages"`
	Temperature float32   `json:"temperature,omitempty"`
	TopP        float32   `json:"top_p,omitempty"`
	MaxTokens   int       `json:"max_tokens,omitempty"`
}

// ChatResponse from OpenAI
type ChatResponse struct {
	Choices []struct {
		Message Message `json:"message"`
	} `json:"choices"`
}

// NPC data
type Npc struct {
	Bio       string `json:"bio"`
	Backstory string `json:"backstory"`
	Affinity  int    `json:"affinity"`
}

// Player state
type PlayerState struct {
	Stats            map[string]int             `json:"stats"`
	Inventory        []string                   `json:"inventory"`
	Journal          []string                   `json:"journal"`
	VisitedLocations []string                   `json:"visited_locations"`
	MapGraph         map[string]map[string]bool `json:"map_graph"`
	CurrentLocation  string                     `json:"current_location"`
}

// SaveData for save/load
type SaveData struct {
	NpcData     map[string]*Npc `json:"npc_data"`
	PlayerState PlayerState     `json:"player_state"`
	History     []Message       `json:"history"`
}

var (
	globalAPIKey        string
	globalModel         string
	pruneEnabled        = true
	npcData             = map[string]*Npc{}
	sceneDescriptions   = map[string]string{}
	itemsData           = map[string]string{}
	playerState         PlayerState
	history             []Message
	summaryPrompt       = "Summarize the following adventure context in two sentences."
	placeholderResponse = "[The realm is silent; no response comes.]"
)

func init() {
	rand.Seed(time.Now().UnixNano())
}

// Normalize multiline text: remove CRs, trim blanks, collapse multiple blanks
func normalizeText(text string) string {
	text = strings.ReplaceAll(text, "\r", "")
	lines := strings.Split(text, "\n")
	for len(lines) > 0 && strings.TrimSpace(lines[0]) == "" {
		lines = lines[1:]
	}
	for len(lines) > 0 && strings.TrimSpace(lines[len(lines)-1]) == "" {
		lines = lines[:len(lines)-1]
	}
	var out []string
	blank := false
	for _, line := range lines {
		if strings.TrimSpace(line) == "" {
			if !blank {
				out = append(out, "")
			}
			blank = true
		} else {
			out = append(out, line)
			blank = false
		}
	}
	return strings.Join(out, "\n")
}

// Title-case each word
func titleCase(s string) string {
	words := strings.Fields(s)
	for i, w := range words {
		if len(w) > 0 {
			words[i] = strings.ToUpper(string(w[0])) + strings.ToLower(w[1:])
		}
	}
	return strings.Join(words, " ")
}

// contains reports whether s is in slice
func contains(slice []string, s string) bool {
	for _, v := range slice {
		if v == s {
			return true
		}
	}
	return false
}

// Call OpenAI API with retries
func callOpenAI(msgs []Message) string {
	req := ChatRequest{Model: globalModel, Messages: msgs, Temperature: 0.8, MaxTokens: 500, TopP: 0.9}
	payload, err := json.Marshal(req)
	if err != nil {
		fmt.Fprintln(os.Stderr, "JSON marshal error:", err)
		return placeholderResponse
	}
	for attempt := 0; attempt < 3; attempt++ {
		httpReq, err := http.NewRequest("POST", "https://api.openai.com/v1/chat/completions", bytes.NewBuffer(payload))
		if err != nil {
			fmt.Fprintln(os.Stderr, "Request error:", err)
			return placeholderResponse
		}
		httpReq.Header.Set("Content-Type", "application/json")
		httpReq.Header.Set("Authorization", "Bearer "+globalAPIKey)
		client := &http.Client{Timeout: 30 * time.Second}
		resp, err := client.Do(httpReq)
		if err != nil {
			fmt.Fprintln(os.Stderr, "API error:", err)
			time.Sleep(1 * time.Second)
			continue
		}
		defer resp.Body.Close()
		body, err := ioutil.ReadAll(resp.Body)
		if err != nil {
			fmt.Fprintln(os.Stderr, "Read error:", err)
			return placeholderResponse
		}
		if resp.StatusCode != http.StatusOK {
			fmt.Fprintln(os.Stderr, "HTTP", resp.StatusCode, string(body))
			time.Sleep(1 * time.Second)
			continue
		}
		var res ChatResponse
		if err := json.Unmarshal(body, &res); err != nil {
			fmt.Fprintln(os.Stderr, "Unmarshal error:", err)
			return placeholderResponse
		}
		if len(res.Choices) > 0 {
			return strings.TrimSpace(res.Choices[0].Message.Content)
		}
		return placeholderResponse
	}
	fmt.Fprintln(os.Stderr, "[Error] Could not reach OpenAI API. Continuing with placeholder response.")
	return placeholderResponse
}

// Prune and summarize history
func pruneHistory(msgs []Message) []Message {
	maxMsgs, keepTail := 30, 10
	if len(msgs) <= maxMsgs {
		return msgs
	}
	toSumm := msgs[:len(msgs)-keepTail]
	tail := msgs[len(msgs)-keepTail:]
	prompt := []Message{{Role: "system", Content: summaryPrompt}}
	prompt = append(prompt, toSumm...)
	summary := callOpenAI(prompt)
	newHist := []Message{{Role: "system", Content: "SUMMARY: " + summary}}
	newHist = append(newHist, tail...)
	fmt.Println(Yellow + "[History pruned and summarized]" + Reset)
	return newHist
}

// List items in scene via AI
func listItems(msgs []Message) []string {
	prompt := append(msgs, Message{Role: "user", Content: "List, in a comma-separated list, all objects present in this scene. If none, reply 'None'."})
	raw := callOpenAI(prompt)
	parts := strings.Split(raw, ",")
	var out []string
	for _, p := range parts {
		name := strings.Trim(strings.TrimSpace(p), ".!?:;")
		if name != "" && strings.ToLower(name) != "none" {
			out = append(out, name)
		}
	}
	return out
}

// List exits via AI
func listExits(msgs []Message) []string {
	prompt := append(msgs, Message{Role: "user", Content: "List, in a comma-separated list, all exits or directions available from this scene. If none, reply 'None'."})
	raw := callOpenAI(prompt)
	parts := strings.Split(raw, ",")
	var out []string
	for _, p := range parts {
		name := strings.Trim(strings.TrimSpace(p), ".!?:;")
		if name != "" && strings.ToLower(name) != "none" {
			out = append(out, name)
		}
	}
	return out
}

// List NPCs via AI
func listNpcs(msgs []Message) []string {
	prompt := append(msgs, Message{Role: "user", Content: "List, in a comma-separated list, the FULL NAMES of all NPCs currently present in this scene. If none, reply 'None'."})
	raw := callOpenAI(prompt)
	parts := strings.Split(raw, ",")
	var out []string
	for _, p := range parts {
		name := strings.Trim(strings.TrimSpace(p), ".!?:;")
		if name != "" && strings.ToLower(name) != "none" {
			out = append(out, name)
		}
	}
	return out
}

// Print environment summary (exits, NPCs, items)
func printEnvironmentSummary(msgs []Message) {
	exits := listExits(msgs)
	npcs := listNpcs(msgs)
	items := listItems(msgs)
	fmt.Printf(Blue+"Exits:"+Reset+" %s\n", strings.Join(exits, ", "))
	fmt.Printf(Green+"NPCs here:"+Reset+" %s\n", strings.Join(npcs, ", "))
	fmt.Printf(Yellow+"Items here:"+Reset+" %s\n", strings.Join(items, ", "))
}

// Initialize new player state
func initPlayerState() {
	stats := map[string]int{}
	for _, s := range []string{"STR", "DEX", "CON", "INT", "WIS", "CHA"} {
		stats[s] = rand.Intn(11) + 8
	}
	playerState = PlayerState{
		Stats:            stats,
		Inventory:        []string{},
		Journal:          []string{},
		VisitedLocations: []string{},
		MapGraph:         map[string]map[string]bool{},
		CurrentLocation:  "",
	}
}

// Save game to JSON file
func saveGame(msgs []Message) {
	d := SaveData{NpcData: npcData, PlayerState: playerState, History: msgs}
	b, err := json.MarshalIndent(d, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, "Save encode error:", err)
		return
	}
	if err := ioutil.WriteFile("savegame.json", b, 0644); err != nil {
		fmt.Fprintln(os.Stderr, "Save file error:", err)
		return
	}
	fmt.Printf(Yellow + "Game saved to savegame.json." + Reset + "\n")
}

// Load game from JSON file
func loadGame() ([]Message, error) {
	b, err := ioutil.ReadFile("savegame.json")
	if err != nil {
		return nil, err
	}
	var d SaveData
	if err := json.Unmarshal(b, &d); err != nil {
		return nil, err
	}
	npcData = d.NpcData
	playerState = d.PlayerState
	fmt.Printf(Yellow + "Game loaded from savegame.json." + Reset + "\n")
	return d.History, nil
}

// Start conversation with NPC
func startConversation(npcName string) {
	if _, ok := npcData[npcName]; !ok {
		last := history
		if len(last) > 6 {
			last = last[len(last)-6:]
		}
		prompt := append(last, Message{Role: "user", Content: fmt.Sprintf(
			"You previously described an NPC named '%s'.\n"+
				"Please provide TWO clearly labeled sections:\n"+
				"BIO: One sentence describing who they are (name/title/role).\n"+
				"BACKSTORY: Two sentences about their past, interests, or beliefs.\n"+
				"Respond exactly in this format.", npcName)})
		summary := callOpenAI(prompt)
		bio, backstory := "", ""
		for _, line := range strings.Split(summary, "\n") {
			up := strings.ToUpper(line)
			if strings.HasPrefix(up, "BIO:") {
				bio = strings.TrimSpace(line[4:])
			}
			if strings.HasPrefix(up, "BACKSTORY:") {
				backstory = strings.TrimSpace(line[9:])
			}
		}
		if bio == "" {
			bio = fmt.Sprintf("%s, a person of note.", npcName)
		}
		if backstory == "" {
			backstory = "They prefer to keep much of their past private."
		}
		npcData[npcName] = &Npc{Bio: bio, Backstory: backstory, Affinity: 0}
	}
	info := npcData[npcName]
	sys := fmt.Sprintf("You are %s.\n%s\nBackstory: %s\n\n"+
		"Speak in first-person as yourself. ALWAYS refer to yourself by that exact name. "+
		"When the player says 'goodbye', 'exit', or 'bye', end the conversation politely.",
		npcName, info.Bio, info.Backstory)
	conv := []Message{{Role: "system", Content: sys}}
	fmt.Printf("\n"+Blue+"— You begin talking with %s. (type 'goodbye' to end) —"+Reset+"\n\n", npcName)
	reader := bufio.NewReader(os.Stdin)
	for {
		fmt.Print("You: ")
		line, _ := reader.ReadString('\n')
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		conv = append(conv, Message{Role: "user", Content: line})
		low := strings.ToLower(line)
		if low == "goodbye" || low == "exit" || low == "bye" {
			farewell := callOpenAI(conv)
			fmt.Printf(Green+"%s:"+Reset+" %s\n\n", npcName, farewell)
			info.Affinity++
			fmt.Println("— Conversation ended. You return to exploration. —\n")
			return
		}
		reply := callOpenAI(conv)
		fmt.Printf(Green+"%s:"+Reset+" %s\n", npcName, reply)
		conv = append(conv, Message{Role: "assistant", Content: reply})
	}
}

// Draw ASCII map recursively
func drawMap(node, parent, prefix string, isLast bool, visited map[string]bool) {
	if visited == nil {
		visited = map[string]bool{node: true}
		fmt.Println(prefix + node)
	} else {
		branch := "├─ "
		if isLast {
			branch = "└─ "
		}
		fmt.Println(prefix + branch + node)
		visited[node] = true
	}
	neighbors := make([]string, 0)
	for n := range playerState.MapGraph[node] {
		neighbors = append(neighbors, n)
	}
	sort.Strings(neighbors)
	children := make([]string, 0)
	for _, c := range neighbors {
		if c == parent || visited[c] {
			continue
		}
		children = append(children, c)
	}
	for i, child := range children {
		last := i == len(children)-1
		nextPrefix := prefix
		if isLast {
			nextPrefix += "   "
		} else {
			nextPrefix += "│  "
		}
		drawMap(child, node, nextPrefix, last, visited)
	}
}

// printHelp displays the list of available commands
func printHelp() {
	fmt.Println()
	fmt.Println("Available commands:")
	fmt.Println("  go to/move to/travel to <location>    - Move to a place or direction")
	fmt.Println("  north/south/east/west                 - Move in a cardinal direction")
	fmt.Println("  look / observe / where                - Describe your surroundings")
	fmt.Println("  examine <object> / look at <object> / inspect <object> - Inspect something")
	fmt.Println("  talk to                              - List NPCs here")
	fmt.Println("  talk to <NPC name>                   - Start conversation with someone")
	fmt.Println("  inventory                            - Show your items")
	fmt.Println("  stats                                - Show your character stats")
	fmt.Println("  journal                              - Show your journal entries")
	fmt.Println("  save                                 - Save your current game")
	fmt.Println("  load                                 - Load a saved game")
	fmt.Println("  map [<location>]                     - Show ASCII map (default=current loc)")
	fmt.Println("  hint                                 - Get an in-game hint")
	fmt.Println("  set prune on|off                     - Enable/disable history summarization")
	fmt.Println("  roll <STAT> [DC]                     - Perform a d20 skill/attribute check")
	fmt.Println("  help / ?                             - Show this help text")
	fmt.Println("  quit / exit / stop                   - End the adventure or exit NPC chat")
	fmt.Println()
}

func main() {
	globalAPIKey = os.Getenv("OPENAI_API_KEY")
	if globalAPIKey == "" {
		fmt.Fprintln(os.Stderr, Red+"OPENAI_API_KEY not set"+Reset)
		os.Exit(1)
	}
	globalModel = "gpt-4.1-mini"
	reader := bufio.NewReader(os.Stdin)

	// Main menu
	fmt.Printf(Blue + "Welcome to the Immersive Text Adventure!" + Reset + "\n")
	fmt.Printf("1) New game  2) Load game  3) Quit\n> ")
	choice, _ := reader.ReadString('\n')
	choice = strings.TrimSpace(choice)
	var loaded []Message
	if choice == "2" {
		h, err := loadGame()
		if err != nil {
			fmt.Printf(Red + "No save file found." + Reset + "\n")
			initPlayerState()
		} else {
			loaded = h
			history = h
			if len(history) > 0 && history[len(history)-1].Role == "assistant" {
				fmt.Println(Blue + history[len(history)-1].Content + Reset)
			}
		}
	} else if choice == "3" {
		fmt.Println(Yellow + "Goodbye!" + Reset)
		return
	}
	if len(loaded) == 0 {
		initPlayerState()
		fmt.Println("First, choose when and where your story begins (e.g. Year 1372, Isle of Everdawn)")
		fmt.Print("> ")
		start, _ := reader.ReadString('\n')
		start = strings.TrimSpace(start)
		if start == "" {
			start = "Year 1372, in the misty Isle of Everdawn"
		}
		fmt.Println()
		fmt.Println(Blue + "…Very well. Setting the scene…" + Reset + "\n")
		history = []Message{{Role: "system", Content: SYSTEM_PROMPT}, {Role: "user", Content: "Begin the adventure: " + start}}
		intro := normalizeText(callOpenAI(history))
		fmt.Println(Blue + intro + Reset)
		history = append(history, Message{Role: "assistant", Content: intro})
		playerState.CurrentLocation = start
		sceneDescriptions[start] = intro
		playerState.VisitedLocations = append(playerState.VisitedLocations, start)
		printHelp()
	}

	// Game loop
	for {
		loc := playerState.CurrentLocation
		if loc != "" {
			fmt.Printf("%s> ", loc)
		} else {
			fmt.Print("> ")
		}
		cmd, _ := reader.ReadString('\n')
		cmd = strings.TrimSpace(cmd)
		if cmd == "" {
			continue
		}
		lc := strings.ToLower(cmd)
		// toggle prune
		if strings.HasPrefix(lc, "set prune") {
			parts := strings.Fields(lc)
			if len(parts) == 3 && (parts[2] == "on" || parts[2] == "off") {
				pruneEnabled = (parts[2] == "on")
				state := "disabled"
				if pruneEnabled {
					state = "enabled"
				}
				fmt.Printf("History summarization %s.\n", state)
			} else {
				fmt.Println("Usage: set prune on|off")
			}
			continue
		}
		if pruneEnabled {
			history = pruneHistory(history)
		}
		// exit
		switch lc {
		case "quit", "exit", "stop":
			fmt.Println(Yellow + "Farewell, traveler!" + Reset)
			return
		case "help", "?":
			printHelp()
			continue
		case "inventory":
			inv := "Empty"
			if len(playerState.Inventory) > 0 {
				inv = strings.Join(playerState.Inventory, ", ")
			}
			fmt.Printf(Yellow+"Inventory:"+Reset+" %s\n", inv)
			continue
		case "stats":
			for k, v := range playerState.Stats {
				fmt.Printf(" %s: %d\n", k, v)
			}
			continue
		case "journal":
			fmt.Println(Blue + "Journal Entries:" + Reset)
			for _, e := range playerState.Journal {
				fmt.Printf(" - %s\n", e)
			}
			continue
		case "save":
			saveGame(history)
			continue
		case "load":
			if h, err := loadGame(); err == nil {
				history = h
			}
			continue
		case "hint":
			hintPrompt := append(history, Message{Role: "user", Content: fmt.Sprintf("I'm stuck at %s. Please give me a hint.", playerState.CurrentLocation)})
			hint := normalizeText(callOpenAI(hintPrompt))
			fmt.Printf(Yellow+"Hint:"+Reset+" %s\n", hint)
			continue
		}
		// roll
		if strings.HasPrefix(lc, "roll") {
			parts := strings.Fields(cmd)
			if len(parts) >= 2 {
				stat := strings.ToUpper(parts[1])
				if val, ok := playerState.Stats[stat]; ok {
					mod := (val - 10) / 2
					die := rand.Intn(20) + 1
					total := die + mod
					result := fmt.Sprintf("Rolled 1d20 + %d = %d", mod, total)
					if len(parts) >= 3 {
						if dc, err := strconv.Atoi(parts[2]); err == nil {
							outcome := "Failure"
							if total >= dc {
								outcome = "Success"
							}
							result += fmt.Sprintf(" vs DC %d: %s", dc, outcome)
						}
					}
					fmt.Println(Yellow + result + Reset)
				} else {
					fmt.Printf(Red+"Unknown stat '%s'."+Reset+"\n", stat)
				}
			} else {
				fmt.Println("Usage: roll <stat> [DC]")
			}
			continue
		}
		// map
		if strings.HasPrefix(lc, "map") {
			parts := strings.Fields(cmd)
			target := playerState.CurrentLocation
			if len(parts) > 1 {
				target = titleCase(parts[1])
			}
			fmt.Printf(Blue+"Map for '%s':"+Reset+"\n", target)
			if len(playerState.VisitedLocations) > 0 {
				fmt.Printf(Yellow+"Visited:"+Reset+" %s\n", strings.Join(playerState.VisitedLocations, ", "))
			} else {
				fmt.Printf(Yellow + "No visited locations yet." + Reset + "\n")
			}
			if _, ok := playerState.MapGraph[target]; !ok {
				fmt.Printf(Yellow+"No map connections for '%s'."+Reset+"\n", target)
				if desc, ex := sceneDescriptions[target]; ex {
					fmt.Printf("\n"+Green+"Details for '%s':"+Reset+"\n", target)
					for _, line := range strings.Split(desc, "\n") {
						fmt.Printf("  %s\n", line)
					}
				}
				continue
			}
			drawMap(target, "", "", true, nil)
			if desc, ex := sceneDescriptions[target]; ex {
				fmt.Printf("\n"+Green+"Details for '%s':"+Reset+"\n", target)
				for _, line := range strings.Split(desc, "\n") {
					fmt.Printf("  %s\n", line)
				}
			}
			continue
		}
		// talk to (list)
		if lc == "talk to" {
			npcs := listNpcs(history)
			if len(npcs) == 0 {
				fmt.Println(Yellow + "There's no one here to talk to." + Reset)
			} else {
				fmt.Printf(Green+"You can talk to:"+Reset+" %s\n", strings.Join(npcs, ", "))
			}
			continue
		}
		// talk to <name>
		if strings.HasPrefix(lc, "talk to ") {
			name := strings.TrimSpace(cmd[8:])
			if name == "" {
				fmt.Println("Usage: talk to <full NPC name>")
			} else {
				startConversation(name)
			}
			continue
		}
		// look/observe/where
		if lc == "look" || lc == "observe" || lc == "where" {
			history = append(history, Message{Role: "user", Content: cmd})
			desc := normalizeText(callOpenAI(history))
			fmt.Println()
			fmt.Println(Blue + desc + Reset)
			history = append(history, Message{Role: "assistant", Content: desc})
			printEnvironmentSummary(history)
			continue
		}
		// examine / look at / inspect commands
		handled := false
		for _, pref := range []string{"examine ", "look at ", "inspect "} {
			if strings.HasPrefix(lc, pref) {
				target := strings.TrimSpace(cmd[len(pref):])
				if target == "" {
					fmt.Println("Usage: examine <object>")
				} else {
					history = append(history, Message{Role: "user", Content: cmd})
					desc := normalizeText(callOpenAI(history))
					fmt.Println(Blue + desc + Reset)
					itemsData[target] = desc
					playerState.Journal = append(playerState.Journal, fmt.Sprintf("Examined %s.", target))
					history = append(history, Message{Role: "assistant", Content: desc})
				}
				handled = true
				break
			}
		}
		if handled {
			continue
		}
		// movement
		moved := false
		var dest string
		for _, pref := range []string{"go to ", "move to ", "travel to "} {
			if strings.HasPrefix(lc, pref) {
				dest = titleCase(cmd[len(pref):])
				moved = true
				break
			}
		}
		if !moved {
			switch lc {
			case "north", "south", "east", "west":
				dest = titleCase(lc)
				moved = true
			}
		}
		if moved {
			prev := playerState.CurrentLocation
			if prev != "" {
				if playerState.MapGraph[prev] == nil {
					playerState.MapGraph[prev] = map[string]bool{}
				}
				if playerState.MapGraph[dest] == nil {
					playerState.MapGraph[dest] = map[string]bool{}
				}
				playerState.MapGraph[prev][dest] = true
				playerState.MapGraph[dest][prev] = true
			}
			playerState.CurrentLocation = dest
			if !contains(playerState.VisitedLocations, dest) {
				playerState.VisitedLocations = append(playerState.VisitedLocations, dest)
			}
			history = append(history, Message{Role: "user", Content: cmd})
			resp := normalizeText(callOpenAI(history))
			fmt.Println()
			fmt.Println(Blue + resp + Reset)
			history = append(history, Message{Role: "assistant", Content: resp})
			sceneDescriptions[dest] = resp
			printEnvironmentSummary(history)
			continue
		}
		// default forward
		history = append(history, Message{Role: "user", Content: cmd})
		resp := normalizeText(callOpenAI(history))
		fmt.Println()
		fmt.Println(Blue + resp + Reset)
		history = append(history, Message{Role: "assistant", Content: resp})
	}
}
