import re

path = "/Users/omar/Desktop/jarvis/jarvis/agent.py"
with open(path, 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    # Find the line that starts with '    def _request(self) -> dict:' 
    # but might have wrong indentation.
    if line.strip() == 'def _request(self) -> dict:':
        new_lines.append('    def _request(self) -> dict:\n')
    # Fix the lines immediately following it to have 8 spaces
    elif len(new_lines) > 0 and new_lines[-1].strip() == 'def _request(self) -> dict:':
        # This is a bit naive, but let's try to fix the block until the next function or return
        pass
    else:
        new_lines.append(line)

# Let's try a better approach: find the function and rewrite it entirely with correct indentation.
# Since we know the content, we can just use a regex to replace the whole function.

with open(path, 'r') as f:
    content = f.read()

# Match from def _request until return req
pattern = r'(?s)\s*def _request\(self\) -> dict:.*?return req'
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
    '        return req'
)
content = re.sub(pattern, replacement, content)

with open(path, 'w') as f:
    f.write(content)
print("Fixed indentation for _request")
