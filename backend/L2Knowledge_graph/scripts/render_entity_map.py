"""
Render the L2 EntityMap graph to PNG images so the graph can be *seen* (until the
interactive React-Flow query UI lands).

Produces two views in the output dir (default: repo root):
  * L2_entitymap_overview.png  — the whole graph, nodes coloured by entity type
  * L2_entitymap_vessel.png    — a focused, legible view of one vessel's crew with
                                 their certificates, ports and contracts

    GRAPH_BACKEND=age python -m L2Knowledge_graph.scripts.render_entity_map [output_dir] [vessel_name]
"""
import asyncio
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import networkx as nx

from L2Knowledge_graph.graph_db import run_cypher

# colour per entity label
COLOURS = {
    "Crew": "#2563f5",
    "Vessel": "#0e9f6e",
    "Port": "#f59e0b",
    "Certificate": "#9333ea",
    "Contract": "#ef4444",
    "?": "#9ca3af",
}


async def _fetch_all():
    nodes = await run_cypher(
        "MATCH (n) RETURN {id: id(n), label: labels(n)[0], "
        "name: coalesce(n.name, n.type, n.contract_id, n.crew_id)} AS v"
    )
    edges = await run_cypher(
        "MATCH (a)-[r]->(b) RETURN {s: id(a), t: id(b), type: type(r)} AS v"
    )
    nodes = [n for n in nodes if isinstance(n, dict) and n.get("id") is not None]
    edges = [e for e in edges if isinstance(e, dict) and e.get("s") is not None]
    return nodes, edges


def _draw(G, labels, colours, title, path, figsize=(18, 13), k=0.55, font=8, seed=7):
    plt.figure(figsize=figsize)
    pos = nx.spring_layout(G, k=k, seed=seed, iterations=120)
    nx.draw_networkx_edges(G, pos, edge_color="#c8ccd2", width=0.8, arrows=False)
    nx.draw_networkx_nodes(G, pos, node_color=colours, node_size=420,
                           edgecolors="white", linewidths=0.8)
    nx.draw_networkx_labels(G, pos, labels=labels, font_size=font, font_color="#1a2333")
    present = sorted({c for c in colours})
    legend = [mpatches.Patch(color=col, label=lab) for lab, col in COLOURS.items()
              if col in present and lab != "?"]
    plt.legend(handles=legend, loc="upper left", fontsize=11, frameon=True)
    plt.title(title, fontsize=16, fontweight="bold", color="#182947")
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(path, dpi=130, bbox_inches="tight")
    plt.close()
    print("wrote", path)


async def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    focus_crew = sys.argv[2] if len(sys.argv) > 2 else "Miguel Torres"

    nodes, edges = await _fetch_all()
    if not nodes:
        print("No graph data. Run with GRAPH_BACKEND=age after seeding (L2Knowledge_graph.scripts.seed_entity_map).")
        return

    meta = {n["id"]: n for n in nodes}

    # ── overview: the whole graph ────────────────────────────────────────────────
    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n["id"])
    for e in edges:
        if e["s"] in meta and e["t"] in meta:
            G.add_edge(e["s"], e["t"])
    labels = {nid: (m["name"] or "")[:16] for nid, m in meta.items()}
    cols = [COLOURS.get(meta[nid]["label"], COLOURS["?"]) for nid in G.nodes()]
    _draw(G, labels, cols,
          f"L2 EntityMap - full graph  ({len(nodes)} nodes, {len(edges)} edges)",
          f"{out_dir}/L2_entitymap_overview.png", figsize=(20, 14), k=0.6, font=7)

    # ── focused: ONE crew member's neighbourhood (2 hops OUTWARD only) ────────────
    # Walking outgoing edges from a single crew keeps the picture legible and shows
    # every edge type: HOLDS, CURRENTLY_AT, ASSIGNED_TO, SIGNED, then the contract's
    # FOR_VESSEL/AT_PORT and the vessel's CALLS_AT on the 2nd hop. Expanding through
    # the (highly shared) Port nodes would pull in the whole graph, so we don't.
    seed_ids = [n["id"] for n in nodes
                if n["label"] == "Crew" and n.get("name") == focus_crew]
    if not seed_ids:
        # focus_crew may be a crew_id rather than a name — match the node whose name
        # the builder set, else fall back to the first crew with a contract.
        signed_src = {e["s"] for e in edges if e["type"] == "SIGNED"}
        seed_ids = [nid for nid in signed_src][:1]
    keep = set(seed_ids)
    frontier = set(seed_ids)
    for _hop in range(2):
        nxt = set()
        for e in edges:
            if e["s"] in frontier:
                keep.add(e["t"]); nxt.add(e["t"])
        frontier = nxt
    focus_name = next((meta[s]["name"] for s in seed_ids if s in meta), focus_crew)

    H = nx.DiGraph()
    for nid in keep:
        H.add_node(nid)
    edge_labels = {}
    for e in edges:
        if e["s"] in keep and e["t"] in keep:
            H.add_edge(e["s"], e["t"])
            edge_labels[(e["s"], e["t"])] = e["type"]
    hlabels = {nid: (meta[nid]["name"] or "")[:18] for nid in H.nodes()}
    hcols = [COLOURS.get(meta[nid]["label"], COLOURS["?"]) for nid in H.nodes()]

    plt.figure(figsize=(16, 11))
    pos = nx.spring_layout(H, k=0.9, seed=3, iterations=200)
    nx.draw_networkx_edges(H, pos, edge_color="#b8bec7", width=1.1, arrows=True,
                           arrowsize=12, arrowstyle="-|>", connectionstyle="arc3,rad=0.04")
    nx.draw_networkx_nodes(H, pos, node_color=hcols, node_size=900,
                           edgecolors="white", linewidths=1.2)
    nx.draw_networkx_labels(H, pos, labels=hlabels, font_size=9, font_color="#10192b")
    nx.draw_networkx_edge_labels(H, pos, edge_labels=edge_labels, font_size=7,
                                 font_color="#5a6069", rotate=False,
                                 bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.7))
    present = {COLOURS.get(meta[nid]["label"]) for nid in H.nodes()}
    legend = [mpatches.Patch(color=col, label=lab) for lab, col in COLOURS.items()
              if col in present and lab != "?"]
    plt.legend(handles=legend, loc="upper left", fontsize=11, frameon=True)
    plt.title(f"L2 EntityMap - focus: {focus_name}  ({H.number_of_nodes()} nodes)",
              fontsize=15, fontweight="bold", color="#182947")
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(f"{out_dir}/L2_entitymap_vessel.png", dpi=140, bbox_inches="tight")
    plt.close()
    print("wrote", f"{out_dir}/L2_entitymap_vessel.png")


if __name__ == "__main__":
    asyncio.run(main())
