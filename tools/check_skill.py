#!/usr/bin/env python3
"""Checks the SKILL.md frontmatter against the claude.ai upload limits (name, description ≤ 1024 characters)."""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "skills/website-check/SKILL.md"
text = open(path, encoding="utf-8").read()
m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
if not m:
    sys.exit(f"{path}: YAML-Frontmatter (--- … ---) fehlt")
fields = dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M))
errors = []
if not fields.get("name"):
    errors.append("name fehlt")
elif len(fields["name"]) > 64 or not re.fullmatch(r"[a-z0-9-]+", fields["name"]):
    errors.append(f"name «{fields['name']}»: höchstens 64 Zeichen, nur a-z, 0-9 und -")
desc = fields.get("description", "")
if not desc:
    errors.append("description fehlt")
elif len(desc) > 1024:
    errors.append(f"description hat {len(desc)} Zeichen (erlaubt: 1024)")
if errors:
    sys.exit(f"{path}: " + "; ".join(errors))
print(f"{path}: ok (name «{fields['name']}», description {len(desc)}/1024 Zeichen)")
