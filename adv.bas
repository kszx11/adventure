' adv.bas ΓÇô MMBasic Text Adventure (no line numbers)
'
'--- User configuration ------------------------------------------------
APIKEY$   = "YOUR_OPENAI_API_KEY"
MODEL$    = "gpt-3.5-turbo"
APIURL$   = "https://api.openai.com/v1/chat/completions"
SYSTEM$   = "You are Realmweaver, the narrator and engine of an immersive, open-ended text adventure. " + _
           "Whenever you describe people in a scene, ALWAYS give them: 1) A full name and title/role; " + _
           "2) A brief backstory snippet. The world is full of towns, villages, forests, cities, ruins, " + _
           "markets, taverns, temples, homes, and travelers. Maintain context of location and NPCs. "  + _
           "When the player says 'goodbye', end the conversation politely."
'
'--- Wi-Fi configuration (fill in your own) --------------------------------
SSID$     = "YourWiFiSSID"
PASS$     = "YourWiFiPassword"
'
'--- Subroutine: call the OpenAI chat completion and extract the content ----
SUB CallAI (BYVAL Prompt$, BYREF Reply$)
  DIM Body$, Resp$
  Body$ = "{""model"":""" + MODEL$ + """,""messages"": [{""role"":""system"",""content"":""" + SYSTEM$ + """},{""role"":""user"",""content"":""" + Prompt$ + """}]}"
  HTTPPOST APIURL$, Body$, Resp$, 15000, "Authorization: Bearer " + APIKEY$ + ",Content-Type: application/json"
  POS = INSTR(Resp$, """content"":""")
  IF POS THEN
    START_POS = POS + LEN("""content"":""")
    END_POS   = INSTR(START_POS, Resp$, """")
    Reply$    = MID$(Resp$, START_POS, END_POS - START_POS)
  ELSE
    Reply$ = "(No response)"
  END IF
END SUB
'
'--- Subroutine: print help text ------------------------------------------
SUB ShowHelp
  PRINT
  PRINT "Available commands:"
  PRINT "  help         - Show this help text"
  PRINT "  quit / exit  - End the adventure"
  PRINT "  Any other    - input is forwarded to the AI"
  PRINT
END SUB
'
'--- Main -----------------------------------------------------------------
PRINT "Connecting to Wi-Fi..."
WIFI CONNECT SSID$,PASS$
IF WIFISTATUS<>1 THEN
  PRINT "Failed to connect to Wi-Fi."
  END
END IF
PRINT "Wi-Fi connected."

PRINT
PRINT "Welcome to the MMBasic Text Adventure!"
PRINT "Enter starting context (e.g. Year 1372, Isle of Everdawn):"
INPUT ">"; Start$
Prompt$ = "Begin the adventure: " + Start$
CallAI Prompt$, Reply$
PRINT : PRINT Reply$ : PRINT

ShowHelp

DO
  INPUT ">"; Cmd$
  IF LEN(Cmd$)=0 THEN CONTINUE DO
  L$ = LOWER$(Cmd$)
  SELECT CASE L$
    CASE "quit", "exit"
      PRINT "Farewell, traveler!"
      END
    CASE "help"
      ShowHelp
    CASE ELSE
      Prompt$ = Cmd$
      CallAI Prompt$, Reply$
      PRINT : PRINT Reply$ : PRINT
  END SELECT
LOOP
