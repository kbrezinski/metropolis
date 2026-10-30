"""Draw a topology as an image, using networkx for the layout.

The graph is built from the topology, laid out by networkx, and drawn with the
matplotlib functions networkx provides for the purpose. Node colours follow the
node's type, endpoints are labelled with their address, and a link is labelled
with its kind and VLANs.

matplotlib and networkx are imported inside the functions, so the topology
library itself depends on neither. Install them when you want to draw.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from metropolis.topology import Topology

# Colour per node type.
COLOURS = {
    "router": "#ffd166",
    "switch": "#8ecae6",
    "segment": "#b7e4c7",
    "device": "#e0e0e0",
}

# Edge colour per link kind. Red is deliberately avoided: in an OT diagram red
# reads as a fault, and a trunk is normal.
LINK_COLOURS = {
    "trunk": "#6a4c93",
    "access": "#4a4e69",
    "transit": "#0077b6",
    "underlay": "#2a9d8f",
    "overlay": "#b5179e",
}


def _graphs(topology: Topology):
    """The topology as a networkx graph, plus the labels to draw."""
    import networkx as nx

    graph = nx.Graph()
    kinds: dict[str, str] = {}
    labels: dict[str, str] = {}

    for router in topology.routers:
        kinds[router.name] = "router"
    for switch in topology.switches:
        kinds[switch.name] = "switch"
    for segment in topology.segments:
        kinds[segment.name] = "segment"
    for device in topology.devices:
        kinds[device.name] = "device"

    addresses = {d.name: d.address for d in topology.devices if d.address}
    for name, kind in kinds.items():
        address = addresses.get(name)
        graph.add_node(name, kind=kind)
        labels[name] = f"{name}\n{address}" if address else name

    for link in topology.links:
        first, second = link.endpoints
        if first.node in kinds and second.node in kinds:
            graph.add_edge(
                first.node,
                second.node,
                kind=link.kind,
                vlans=tuple(link.vlans),
                label=_edge_label(link),
            )
    return graph, kinds, labels


def _edge_label(link: Any) -> str:
    return link.kind + (
        " " + ",".join(str(v) for v in link.vlans) if link.vlans else ""
    )


def available() -> bool:
    """True when the drawing dependencies can be imported."""
    try:
        import matplotlib  # noqa: F401
        import networkx  # noqa: F401
    except ImportError:
        return False
    return True


def render(
    topology: Topology,
    out: str | Path,
    *,
    title: str | None = None,
    dpi: int = 130,
    note: str | None = None,
    seed: int = 7,
) -> Path:
    """Draw a topology and write it to ``out``.

    The file type follows the extension, so ``.png``, ``.svg`` and ``.pdf`` all
    work. ``seed`` fixes the layout, because a force-directed layout is random
    unless it is told not to be.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.patches as mpatches
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D
        import networkx as nx
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            "Drawing needs matplotlib and networkx. "
            "Install them with: uv pip install matplotlib networkx"
        ) from exc

    graph, kinds, labels = _graphs(topology)
    if not graph:
        raise ValueError("there is nothing to draw: the topology has no nodes")

    target = Path(out)

    # Lay out the cabled network on its own. Disconnected nodes otherwise drift
    # toward the edge of the spring layout and leave a large empty canvas.
    components = sorted(nx.connected_components(graph), key=lambda nodes: -len(nodes))
    main = graph.subgraph(components[0])
    positions = nx.spring_layout(main, seed=seed, k=1.0, iterations=400, scale=1.0)
    if len(components) > 1:
        xs = [point[0] for point in positions.values()]
        ys = [point[1] for point in positions.values()]
        center_x = (min(xs) + max(xs)) / 2
        center_y = (min(ys) + max(ys)) / 2
        quadrants = [(-1, 1), (1, 1), (-1, -1), (1, -1)]
        side_x, side_y = min(
            quadrants,
            key=lambda side: sum(
                (x - center_x) * side[0] >= 0 and (y - center_y) * side[1] >= 0
                for x, y in positions.values()
            ),
        )
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        rows = min(6, len(components) - 1)
        for index, nodes in enumerate(components[1:]):
            column, row = divmod(index, rows)
            group_x = center_x + side_x * width * (0.37 + column * 0.1)
            group_y = center_y + side_y * height * (0.12 + 0.36 * (row + 0.5) / rows)
            if len(nodes) == 1:
                positions[next(iter(nodes))] = (group_x, group_y)
            else:
                local = nx.spring_layout(
                    graph.subgraph(nodes), seed=seed, iterations=400, scale=0.1
                )
                for node, point in local.items():
                    positions[node] = (group_x + point[0], group_y + point[1])

    # Scale the canvas and the nodes together. A force layout spreads over
    # [0, 1] however few nodes there are, and node sizes are in points, so a
    # size that suits 48 nodes swamps a topology of four.
    scale = min(1.0, max(0.42, len(graph) / 30))
    node_size = 1200 * scale
    font_size = 8.0 * scale
    # Give large testbeds room for labels without stretching the drawing.
    canvas_scale = min(1.6, max(1.0, len(graph) / 30))
    figure, axes = plt.subplots(
        figsize=(16 * scale * canvas_scale, 12 * scale * canvas_scale)
    )

    nx.draw_networkx_edges(
        graph,
        positions,
        ax=axes,
        edge_color=[
            LINK_COLOURS.get(graph[a][b]["kind"], "#555555") for a, b in graph.edges()
        ],
        width=[
            2.0 if graph[a][b]["kind"] == "trunk" else 1.0 for a, b in graph.edges()
        ],
        style=[
            (0, (1, 0)) if graph[a][b]["kind"] == "trunk" else (0, (4, 3))
            for a, b in graph.edges()
        ],
    )
    nx.draw_networkx_edge_labels(
        graph,
        positions,
        ax=axes,
        edge_labels=nx.get_edge_attributes(graph, "label"),
        font_size=6 * max(scale, 0.7),
        bbox={
            "boxstyle": "round,pad=0.1",
            "facecolor": "white",
            "edgecolor": "none",
            "alpha": 0.75,
        },
    )
    nx.draw_networkx_nodes(
        graph,
        positions,
        ax=axes,
        node_color=[COLOURS.get(kinds[n], COLOURS["device"]) for n in graph.nodes()],
        node_shape="o",
        node_size=node_size,
        edgecolors="#5f6368",
        linewidths=0.8,
    )
    nx.draw_networkx_labels(
        graph, positions, ax=axes, labels=labels, font_size=font_size
    )

    axes.set_title(title or topology.testbed_id, fontsize=16, fontweight="bold")
    # Frame the nodes rather than the whole unit square. networkx lays out over
    # [0, 1] but often uses only part of it, which would leave the drawing
    # adrift in empty space.
    xs = [point[0] for point in positions.values()]
    ys = [point[1] for point in positions.values()]
    margin = 0.18
    if max(xs) - min(xs) > 0:
        axes.set_xlim(min(xs) - margin, max(xs) + margin)
    if max(ys) - min(ys) > 0:
        axes.set_ylim(min(ys) - margin, max(ys) + margin)
    axes.axis("off")

    counts: dict[str, int] = {}
    for kind in kinds.values():
        counts[kind] = counts.get(kind, 0) + 1
    legend = [
        mpatches.Patch(
            facecolor=COLOURS[kind],
            edgecolor="#5f6368",
            label=f"{kind} ({counts.get(kind, 0)})",
        )
        for kind in ("router", "switch", "segment", "device")
        if counts.get(kind)
    ]
    legend.append(mpatches.Patch(facecolor=LINK_COLOURS["trunk"], label="trunk link"))
    legend.append(mpatches.Patch(facecolor=LINK_COLOURS["access"], label="other link"))
    if any(graph[a][b]["kind"] == "overlay" for a, b in graph.edges()):
        legend.append(
            Line2D(
                [0],
                [0],
                color=LINK_COLOURS["overlay"],
                linestyle="--",
                label="overlay (logical)",
            )
        )
    axes.legend(handles=legend, loc="lower left", fontsize=8, framealpha=0.9)
    if note:
        figure.text(0.5, 0.01, note, ha="center", fontsize=8, color="#5f6368")

    figure.tight_layout()
    figure.savefig(target, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return target
