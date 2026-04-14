import re

with open("index-Qe0xmgYd.js", "r", encoding="utf-8") as f:
    c = f.read()

print([m.group(0) for m in re.finditer(r'.{0,40}baseURL.{0,40}', c)])
