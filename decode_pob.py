"""Quick script to decode a POB import code and inspect the XML."""
import zlib, base64, sys

code = sys.argv[1] if len(sys.argv) > 1 else input("Paste POB code: ").strip()
normalized = code.replace("-", "+").replace("_", "/")
compressed = base64.b64decode(normalized)
xml = zlib.decompress(compressed).decode("utf-8")

# Just print the Tree/Spec section
import re
# Find the Spec tag to see how nodes are stored
spec_match = re.search(r'<Spec[^>]*>', xml)
if spec_match:
    print("=== Spec tag ===")
    print(spec_match.group(0)[:500])
    print()

# Count nodes
nodes_match = re.search(r'nodes="([^"]*)"', xml)
if nodes_match:
    nodes = nodes_match.group(1).split(",")
    print(f"=== {len(nodes)} nodes allocated ===")
    print(f"First 20: {nodes[:20]}")
    print()

# Print first 2000 chars of XML for structure overview
print("=== XML structure (first 2000 chars) ===")
print(xml[:2000])
