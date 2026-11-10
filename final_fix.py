import re

path = "/Users/omar/Desktop/jarvis/jarvis/agent.py"
with open(path, 'r') as f:
    content = f.read()

# Fix the syntax error: the def _request was accidentally merged into the previous line.
# We find the merged line and split it.
content = content.replace('return [d for d in defs if d.get("name") in scope]    def _request(self) -> dict:', 
                         'return [d for d in defs if d.get("name") in scope]\n\n    def _request(self) -> dict:')

# Ensure the body of _request is correctly indented (8 spaces).
# The previous patch seems to have used 8 spaces, but we'll verify.
# We look for the lines immediately after 'def _request(self) -> dict:'
# and make sure they start with 8 spaces.

lines = content.splitlines()
new_lines = []
in_request = False
for line in lines:
    if 'def _request(self) -> dict:' in line:
        new_lines.append(line)
        in_request = True
        continue
    if in_request:
        if line.strip() == '' :
            new_lines.append(line)
        elif line.startswith('    def') or line.startswith('    class'):
            in_request = False
            new_lines.append(line)
        else:
            # Ensure 8 spaces of indentation
            stripped = line.lstrip()
            new_lines.append('        ' + stripped)
    else:
        new_lines.append(line)

with open(path, 'w') as f:
    f.write('\n'.join(new_lines) + '\n')
print("Final indentation and syntax fix applied.")
