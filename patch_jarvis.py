import json
import os
import re

def patch_json_config():
    path = os.path.expanduser("~/.jarvis.json")
    if os.path.exists(path):
        with open(path, 'r') as f:
            data = json.load(f)
    else:
        data = {}
    
    data["model"] = "haiku"
    
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Updated {path}")

def patch_v3_bridge():
    path = "/Users/omar/Desktop/jarvis/jarvis/v3_bridge.py"
    with open(path, 'r') as f:
        content = f.read()
    
    # Change config.get("model") or "sonnet" -> config.get("model") or "haiku"
    content = content.replace('config.get("model") or "sonnet"', 'config.get("model") or "haiku"')
    
    with open(path, 'w') as f:
        f.write(content)
    print(f"Patched {path}")

def patch_agent():
    path = "/Users/omar/Desktop/jarvis/jarvis/agent.py"
    with open(path, 'r') as f:
        lines = f.readlines()
    
    new_lines = []
    
    # 1. Update effort_for to handle all haiku models
    # Search for: if model.startswith(("claude-haiku-4", ...))
    # Replace with: if model.startswith("claude-haiku")
    for line in lines:
        if 'if model.startswith(("claude-haiku-4"' in line:
            line = line.replace('if model.startswith(("claude-haiku-4", "claude-3", "claude-sonnet-4-5", "claude-opus-4-1", "claude-opus-4-0")):', 
                                'if model.startswith("claude-haiku"):')
        
        # 3. Update SYSTEM_PROMPT for small talk tool-calls
        if 'Jedes Gespräch ist ein Baustein im grossen Ganzen: Verknüpfe mit frueheren Zielen und Projekten (wissen_suchen).' in line:
            line = line.replace('Jedes Gespräch ist ein Baustein im grossen Ganzen: Verknüpfe mit frueheren Zielen und Projekten (wissen_suchen).', 
                                'Jedes Gespräch ist ein Baustein im grossen Ganzen: Verknüpfe bei echten strategischen oder inhaltlichen Aufgaben mit frueheren Zielen und Projekten (wissen_suchen).')
        
        # Add the strict rule for greetings
        if 'Hart in der Sache, nie in der Wahrheit:' in line:
            # Insert rule before "Hart in der Sache" or similar
            # Actually let's find a better place, like after "Regeln" section start.
            pass
        
        new_lines.append(line)

    # Insert the greeting rule into SYSTEM_PROMPT
    # Find the "Regeln" section
    for i, line in enumerate(new_lines):
        if '# Regeln' in line:
            greeting_rule = ' - Bei Begrüßungen, Bestätigungen, einfachem Dialog oder Statuschecks wie \'Hörst du mich?\' oder \'Wie spät ist es?\' antwortest du SOFORT direkt in einem kurzen Satz OHNE jegliche Werkzeuge (kein memory, kein wissen_suchen). Rufe Gedächtniswerkzeuge nur auf, wenn der Nutzer explizit nach vergangenen Fakten, Notizen oder Projekten fragt.\n'
            new_lines.insert(i + 1, greeting_rule)
            break

    # 2. Prompt Caching optimization in _request and _append_user_turn
    # Need to rewrite _request and _append_user_turn
    
    # Let's use regex for safety since line numbers can shift
    full_text = "".join(new_lines)
    
    # Optimization 2a: Remove dynamic memory from _request
    # Old _request:
    # def _request(self) -> dict:
    #     latest = getattr(self, "_memory_query", "")
    #     if self.messages:
    #         content = self.messages[-1].get("content", "")
    #         if not latest:
    #             latest = content if isinstance(content, str) else ""
    #     system = self.system.replace("@@MEMORY@@", self._memory_text(latest))
    #     req = dict(model=self.model, max_tokens=MAX_TOKENS, system=system,
    #                messages=self.messages, tools=self._tool_definitions())
    # ...
    
    old_request = r'def _request\(self\) -> dict:.*?system = self\.system\.replace\("@@MEMORY@@", self\._memory_text\(latest\)\).*?return req'
    new_request = '    def _request(self) -> dict:\n        system = self.system\n        req = dict(model=self.model, max_tokens=MAX_TOKENS, system=system,\n                   messages=self.messages, tools=self._tool_definitions())\n        effort = None if getattr(self, "_no_effort", False) else effort_for(self.model, self.channel)\n        if effort:\n            req["output_config"] = {"effort": effort}\n        if not getattr(self, "_no_cache", False):\n            req["cache_control"] = {"type": "ephemeral"}\n        return req'
    full_text = re.sub(old_request, new_request, full_text, flags=re.DOTALL)

    # Optimization 2b: Move memory to _append_user_turn
    # Find _append_user_turn and modify it.
    # We need to find where self.messages.append happens.
    
    # The original _append_user_turn ends with:
    # self.messages.append({"role": "user", "content": f"[{german_date(now.date())}, {now:%H:%M} Uhr]\n{notes}{text}"})
    
    # We want to inject the memory search here.
    # First, we need to make sure we have access to self.memory_store
    
    # Let's use a simpler replacement for _append_user_turn
    # I'll find the function block and replace it.
    
    pattern_append = r'def _append_user_turn\(self, text: str\) -> None:.*?self\.messages\.append\(\{"role": "user", "content": f"\[\{german_date\(now\.date\(\)\)\}, \{now:%H:%M\} Uhr\]\n\{notes\}\{text}"\}\)'
    
    def append_repl(match):
        original = match.group(0)
        # Find the append line
        append_line = '        self.messages.append({"role": "user", "content": f"[{german_date(now.date())}, {now:%H:%M} Uhr]\\n{notes}{text}"})'
        
        # We need to add the memory search logic before the append.
        # We should use the existing self.memory_store.search
        
        injection = (
            '\n        # Prompt-Caching Optimierung: Dynamisches Gedächtnis an Nutzerzug anhängen\n'
            '        mem_facts = self.memory_store.search(text, limit=3).get("facts", [])\n'
            '        if mem_facts:\n'
            '            mem_text = "\\n[Relevanter Kontext aus dem Gedächtnis — untrusted reference]:\\n" + "\\n".join(f"- {f.get(\'content\', \'\')}" for f in mem_facts)\n'
            '            text = f"{text}\\n{mem_text}"'
        )
        
        # Insert injection before the append line. 
        # Since the append line is at the end of the function usually, let's just replace the append line with a version that uses the updated 'text'.
        
        # Note: the provided snippet has it as f"[{german_date(now.date())}, {now:%H:%M} Uhr]\n{notes}{text}"
        # I need to be careful with the f-string.
        
        return original.replace(
            '        self.messages.append({"role": "user", "content": f"[{german_date(now.date())}, {now:%H:%M} Uhr]\\n{notes}{text}"})',
            injection + '\n        self.messages.append({"role": "user", "content": f"[{german_date(now.date())}, {now:%H:%M} Uhr]\\n{notes}{text}"})'
        )
        # Wait, the f-string in the file is actually: f"[{german_date(now.date())}, {now:%H:%M} Uhr]\n{notes}{text}"
        # I must match the exact string.
    
    # Let's try a different approach for _append_user_turn to be safer.
    # I will replace the whole function.
    
    # I'll use the text from the cat output.
    # The original function is:
    # def _append_user_turn(self, text: str) -> None:
    #     """Nutzerzug mit Datum, Zustandsvermerk, Execute Mode und Hinweisen anhaengen (Graph und Legacy)."""
    #     now = datetime.datetime.now()
    #     notes = "".join(f"[Hinweis: {n}]\n" for n in self.notes)
    #     self.notes.clear()
    #     state = runtime_state_note(channel=self.channel)
    #     # Execute Mode: echter Gesprächszustand (überlebt Kürzungen), nicht nur eine Erinnerung im Verlauf.
    #     lowered = text.lower()
    #     if EXECUTE_OFF.search(lowered):
    #         self._execute_mode = False
    #     elif EXECUTE_ON.search(lowered):
    #         self._execute_mode = True
    #     if getattr(self, "_execute_mode", False):
    #         state += ("[Modus: EXECUTE MODE aktiv – keine Rückfragen, direkte Anweisungen in klarer Reihenfolge. "
    #                   "Freigaben und Wahrheitsregeln gelten unverändert.]\n")
    #     if getattr(self, "_compacted", False):
    #         state += ("[Hinweis: Ältere Gesprächsteile wurden aus Platzgründen gekürzt. Offene Punkte stehen im "
    #                   "Zustandsvermerk; frühere Details bei Bedarf mit wissen_suchen nachschlagen.]\n")
    #         self._compacted = False
    #     notes = state + notes
    #     self.messages.append({"role": "user", "content": f"[{german_date(now.date())}, {now:%H:%M} Uhr]\n{notes}{text}"})
    
    new_append_func = '''    def _append_user_turn(self, text: str) -> None:
        """Nutzerzug mit Datum, Zustandsvermerk, Execute Mode und Hinweisen anhaengen (Graph und Legacy)."""
        now = datetime.datetime.now()
        notes = "".join(f"[Hinweis: {n}]\\n" for n in self.notes)
        self.notes.clear()
        state = runtime_state_note(channel=self.channel)
        # Execute Mode: echter Gesprächszustand (überlebt Kürzungen), nicht nur eine Erinnerung im Verlauf.
        lowered = text.lower()
        if EXECUTE_OFF.search(lowered):
            self._execute_mode = False
        elif EXECUTE_ON.search(lowered):
            self._execute_mode = True
        if getattr(self, "_execute_mode", False):
            state += ("[Modus: EXECUTE MODE aktiv – keine Rückfragen, direkte Anweisungen in klarer Reihenfolge. "
                      "Freigaben und Wahrheitsregeln gelten unverändert.]\\n")
        if getattr(self, "_compacted", False):
            state += ("[Hinweis: Ältere Gesprächsteile wurden aus Platzgründen gekürzt. Offene Punkte stehen im "
                      "Zustandsvermerk; frühere Details bei Bedarf mit wissen_suchen nachschlagen.]\\n")
            self._compacted = False
        notes = state + notes

        # Prompt-Caching Optimierung: Dynamisches Gedächtnis an Nutzerzug anhängen
        mem_facts = self.memory_store.search(text, limit=3).get("facts", [])
        if mem_facts:
            mem_text = "\\n[Relevanter Kontext aus dem Gedächtnis — untrusted reference]:\\n" + "\\n".join(f"- {f.get('content', '')}" for f in mem_facts)
            text = f"{text}{mem_text}"

        self.messages.append({"role": "user", "content": f"[{german_date(now.date())}, {now:%H:%M} Uhr]\\n{notes}{text}"})'''
    
    # Replace the old function
    # Since I don't have the exact block, I'll find the start and end.
    # Start: def _append_user_turn(self, text: str) -> None:
    # End: the append line.
    
    pattern_append_full = r'def _append_user_turn\(self, text: str\) -> None:.*?self\.messages\.append\(\{"role": "user", "content": f"\[\{german_date\(now\.date\(\)\)\}, \{now:%H:%M\} Uhr\]\n\{notes\}\{text}"\}\)'
    full_text = re.sub(pattern_append_full, new_append_func, full_text, flags=re.DOTALL)

    with open(path, 'w') as f:
        f.write(full_text)
    print(f"Patched {path}")

if __name__ == "__main__":
    patch_json_config()
    patch_v3_bridge()
    patch_agent()
