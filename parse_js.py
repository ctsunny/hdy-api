import re

with open("index-Qe0xmgYd.js", "r", encoding="utf-8") as f:
    content = f.read()

# Extract functions containing rm.post (assuming rm is Axios instance)
posts = re.findall(r'function [a-zA-Z0-9_]+\([^)]*\)\s*\{[^{]*rm\.(?:post|get)\([^{]*\{[^}]*\}[^}]*\)', content)
for p in posts:
    print(p)

print("-----")
posts2 = re.findall(r'rm\.post\((.*?)\)', content)
for p in posts2:
    print(p)

