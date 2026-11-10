import re

path = "/Users/omar/Desktop/jarvis/jarvis/agent.py"
with open(path, 'r') as f:
    content = f.read()

# We will use a regex to replace the whole _request function with the correct indentation.
# The pattern matches from 'def _request' until the line before 'def ask'
pattern = r'    def _request\(self\) -> dict:.*?(\n\n    def ask|(?=\n\s*def ask))'
replacement = (
    '    def _request(self) -> dict:\n'
    '        system = self.system\n'
    '        req = dict(model=self.model, max_tokens=MAX_TOKENS, system=system,\n'
    '                   messages=self.messages, tools=self._tool_definitions())\n'
    '        effort = None if getattr(self, "_no_effort", False) else effort_for(self.model, self.channel)\n'
    '        if effort:\n'
    '            req["output_config"] = {"effort": effort}\n'
    '        if not getattr(self, "_no_cache", False):\n'
    '            req["cache_control"] = {"type": "ephemeral"}\n'
    '        return req\n'
)

# Since the regex might consume the start of 'def ask', we use a group or just append it back.
# Actually, a simpler way is to replace the block specifically.

def replace_request(match):
    return replacement + match.group(1) if match.group(1) else replacement

content = re.sub(pattern, replace_request, content, flags=re.DOTALL)

with open(path, 'w') as f:
    f.write(content)
print("Fixed _request function indentation.")
