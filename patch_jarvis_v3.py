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
    content = content.replace('config.get("model") or "sonnet"', 'config.get("model") or "haiku"')
    with open(path, 'w') as f:
        f.write(content)
    print(f"Patched {path}")

def patch_agent():
    path = "/Users/omar/Desktop/jarvis/jarvis/agent.py"
    with open(path, 'r') as f:
        full_text = f.read()

    # 1. Effort for Haiku
    full_text = re.sub(
        r'if model\.startswith\(\("claude-haiku-4", "claude-3", "claude-sonnet-4-5", "claude-opus-4-1", "claude-opus-4-0"\):\s+return None',
        'if model.startswith("claude-haiku"):\n        return None',
        full_text
    )

    # 2. Small talk rules in SYSTEM_PROMPT
    full_text = full_text.replace(
        'Jedes Gespräch ist ein Baustein im grossen Ganzen: Verknüpfe mit frueheren Zielen und Projekten (wissen_suchen).',
        'Jedes Gespräch ist ein Baustein im grossen Ganzen: Verknüpfe bei echten strategischen oder inhaltlichen Aufgaben mit frueheren Zielen und Projekten (wissen_suchen).'
    )
    
    greeting_rule = '\n- Bei Begrüßungen, Bestätigungen, einfachem Dialog oder Statuschecks wie \'Hörst du mich?\' oder \'Wie spät ist es?\' antwortest du SOFORT direkt in einem kurzen Satz OHNE jegliche Werkzeuge (kein memory, kein wissen_suchen). Rufe Gedächtniswerkzeuge nur auf, wenn der Nutzer explizit nach vergangenen Fakten, Notizen oder Projekten fragt.'
    full_text = full_text.replace('# Regeln', '# Regeln' + greeting_rule)

    # 3. Prompt Caching: Update _request (Fixing Indentation)
    # We match the function signature and the return req
    # Using a more specific regex that targets the whole function block
    # Note the 4-space indent for class methods
    
    old_request_pattern = r'    def _request\(self\) -> dict:.*?return req'
    new_request = (
        '    def _request(self) -> dict:\n'
        '        system = self.system\n'
        '        req = dict(model=self.model, max_tokens=MAX_TOKENS, system=system,\n'
        '                   messages=self.messages, tools=self._tool_definitions())\n'
        '        effort = None if getattr(self, "_no_effort", False) else effort_for(self.model, self.channel)\n'
        '        if effort:\n'
        '            req["output_config"] = {"effort": effort}\n'
        '        if not getattr(self, "_no_cache", False):\n'
        '            req["cache_control"] = {"type": "ephemeral"}\n'
        '        return req'
    )
    full_text = re.sub(old_request_pattern, new_request, full_text, flags=re.DOTALL)

    # 4. Prompt Caching: Update _append_user_turn
    old_append_pattern = r'    def _append_user_turn\(self, text: str\) -> None:.*?self\.messages\.append\(\{"role": "user", "content": f"\[\{german_date\(now\.date\(\)\)\}, \{now:%H:%M\} Uhr\]\n\{notes\}\{text}"\}\)'
    
    new_append_func = (
        '    def _append_user_turn(self, text: str) -> None:\n'
        '        """Nutzerzug mit Datum, Zustandsvermerk, Execute Mode und Hinweisen anhaengen (Graph und Legacy)."""\n'
        '        now = datetime.datetime.now()\n'
        '        notes = "".join(f"[Hinweis: {n}]\\n" for n in self.notes)\n'
        '        self.notes.clear()\n'
        '        state = runtime_state_note(channel=self.channel)\n'
        '        # Execute Mode: echter Gesprächszustand (überlebt Kürzungen), nicht nur eine Erinnerung im Verlauf.\n'
        '        lowered = text.lower()\n'
        '        if EXECUTE_OFF.search(lowered):\n'
        '            self._execute_mode = False\n'
        '        elif EXECUTE_ON.search(lowered):\n'
        '            self._execute_mode = True\n'
        '        if getattr(self, "_execute_mode", False):\n'
        '            state += ("[Modus: EXECUTE MODE aktiv – keine Rückfragen, direkte Anweisungen in klarer Reihenfolge. " \n'
        '                      "Freigaben und Wahrheitsregeln gelten unverändert.]\\n")\n'
        '        if getattr(self, "_compacted", False):\n'
        '            state += ("[Hinweis: Ältere Gesprächsteile wurden aus Platzgründen gekürzt. Offene Punkte stehen im " \n'
        '                      "Zustandsvermerk; frühere Details bei Bedarf mit wissen_suchen nachschlagen.]\\n")\n'
        '            self._compacted = False\n'
        '        notes = state + notes\n\n'
        '        # Prompt-Caching Optimierung: Dynamisches Gedächtnis an Nutzerzug anhängen\n'
        '        mem_facts = self.memory_store.search(text, limit=3).get("facts", [])\n'
        '        if mem_facts:\n'
        '            mem_text = "\\n[Relevanter Kontext aus dem Gedächtnis — untrusted reference]:\\n" + "\\n".join(f"- {f.get(\'content\', \'\')}" for f in mem_facts)\n'
        '            text = f"{text}{mem_text}"\n\n'
        '        self.messages.append({"role": "user", "content": f"[{german_date(now.date())}, {now:%H:%M} Uhr]\\n{notes}{text}"})'
    )
    full_text = re.sub(old_append_pattern, new_append_func, full_text, flags=re.DOTALL)

    with open(path, 'w') as f:
        f.write(full_text)
    print(f"Patched {path}")

if __name__ == "__main__":
    patch_json_config()
    patch_v3_bridge()
    patch_agent()
