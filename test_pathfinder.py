"""Quick test to verify the tree pathfinder works."""
from tree_pathfinder import compute_allocated_nodes

# Test: Witch build targeting some well-known notables
notables = [
    "Elemental Overload",
    "Heart of Thunder",
    "Cruel Preparation",
    "Written in Blood",
    "Discipline and Training",
    "Quick Recovery",
    "Deep Wisdom",
    "Light of Divinity",
    "Arcane Focus",
    "Frost Walker",
]

print("Testing pathfinder with Witch class and 10 notables...")
node_ids, matched, unmatched = compute_allocated_nodes("Witch", notables)

print(f"\nMatched: {len(matched)}")
for m in matched:
    print(f"  ✓ {m}")

print(f"\nUnmatched: {len(unmatched)}")
for u in unmatched:
    print(f"  ✗ {u}")

print(f"\nTotal nodes allocated: {len(node_ids)}")
print(f"Node IDs: {node_ids[:20]}{'...' if len(node_ids) > 20 else ''}")
