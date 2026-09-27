"""Deterministic Q1-Q10 contracts for the pinned Volve slice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import Graph


WELL_ID = "osdu:master-data--Well:15/9-19"
WELL_ID_F1 = "osdu:master-data--Well:15/9-F-1"
WELL_ID_F15 = "osdu:master-data--Well:15/9-F-15"
WELLBORE_ID = "osdu:master-data--Wellbore:NPD-2105"
WELLBORE_ID_A = "osdu:master-data--Wellbore:NPD-3145"
LOG_NAME = "15_9-19_SR_CPI.las"
LOG_NAME_F4 = "NO_15_9-F-4_KLOGH_NEW.las"
EXPECTED_WELLBORES = [
    "15/9-19 A",
    "15/9-19 B",
    "15/9-19 S",
    "15/9-19 SR",
    "15/9-19 SR2",
]
EXPECTED_WELLBORES_F1 = [
    "15/9-F-1",
    "15/9-F-1 A",
    "15/9-F-1 B",
    "15/9-F-1 C",
]
EXPECTED_WELLBORES_F15 = [
    "15/9-F-15",
    "15/9-F-15 A",
    "15/9-F-15 B",
    "15/9-F-15 C",
    "15/9-F-15 D",
]
EXPECTED_LOGS = [
    "15_9-19_SR_CPI.las",
    "STAT1990__30-1__15-9-19_SR__COMPOSITE__1.LAS",
]
EXPECTED_COUNTS = [
    {"label": "Well", "count": 11},
    {"label": "WellLog", "count": 28},
    {"label": "Wellbore", "count": 27},
]


@dataclass(frozen=True)
class Control:
    id: str
    question: str
    query: str
    expected: Any


CONTROLS = (
    Control(
        "Q1",
        "Какие wellbores у скважины 15/9-19?",
        """MATCH (wb:Wellbore)-[:BELONGS_TO_WELL]->(w:Well {osduId: $wellId})
RETURN wb.name AS wellbore ORDER BY wellbore""",
        [{"wellbore": name} for name in EXPECTED_WELLBORES],
    ),
    Control(
        "Q2",
        "Какие well logs у wellbore 15/9-19 SR (NPD-2105)?",
        """MATCH (log:WellLog)-[:BELONGS_TO_WELLBORE]->
      (wb:Wellbore {osduId: $wellboreId})
RETURN log.name AS wellLog ORDER BY wellLog""",
        [{"wellLog": name} for name in EXPECTED_LOGS],
    ),
    Control(
        "Q3",
        "К какой скважине и стволу относится лог 15_9-19_SR_CPI.las?",
        """MATCH (log:WellLog {name: $logName})-[:BELONGS_TO_WELLBORE]->
      (wb:Wellbore)-[:BELONGS_TO_WELL]->(w:Well)
RETURN w.name AS well, wb.name AS wellbore""",
        [{"well": "15/9-19 S", "wellbore": "15/9-19 SR"}],
    ),
    Control(
        "Q4",
        "Какой OSDU ID у ствола 15/9-19 SR?",
        """MATCH (wb:Wellbore {name: '15/9-19 SR'})
RETURN wb.osduId AS osduId""",
        [{"osduId": WELLBORE_ID}],
    ),
    Control(
        "Q5",
        "Какой OSDU ID у скважины 15/9-19 S?",
        """MATCH (w:Well {name: '15/9-19 S'})
RETURN w.osduId AS osduId""",
        [{"osduId": WELL_ID}],
    ),
    Control(
        "Q6",
        "Какие wellbores у скважины 15/9-F-1?",
        """MATCH (wb:Wellbore)-[:BELONGS_TO_WELL]->(w:Well {osduId: $wellIdF1})
RETURN wb.name AS wellbore ORDER BY wellbore""",
        [{"wellbore": name} for name in EXPECTED_WELLBORES_F1],
    ),
    Control(
        "Q7",
        "К какой скважине и стволу относится лог NO_15_9-F-4_KLOGH_NEW.las?",
        """MATCH (log:WellLog {name: $logNameF4})-[:BELONGS_TO_WELLBORE]->
      (wb:Wellbore)-[:BELONGS_TO_WELL]->(w:Well)
RETURN w.name AS well, wb.name AS wellbore""",
        [{"well": "15/9-F-4", "wellbore": "15/9-F-4"}],
    ),
    Control(
        "Q8",
        "Какой OSDU ID у ствола 15/9-19 A?",
        """MATCH (wb:Wellbore {name: '15/9-19 A'})
RETURN wb.osduId AS osduId""",
        [{"osduId": WELLBORE_ID_A}],
    ),
    Control(
        "Q9",
        "Какие wellbores у скважины 15/9-F-15?",
        """MATCH (wb:Wellbore)-[:BELONGS_TO_WELL]->(w:Well {osduId: $wellIdF15})
RETURN wb.name AS wellbore ORDER BY wellbore""",
        [{"wellbore": name} for name in EXPECTED_WELLBORES_F15],
    ),
    Control(
        "Q10",
        "Сколько узлов Well, Wellbore и WellLog в графе?",
        """MATCH (n)
RETURN labels(n)[0] AS label, count(*) AS count
ORDER BY label""",
        EXPECTED_COUNTS,
    ),
)
CONTROL_PARAMS = {
    "wellId": WELL_ID,
    "wellIdF1": WELL_ID_F1,
    "wellIdF15": WELL_ID_F15,
    "wellboreId": WELLBORE_ID,
    "logName": LOG_NAME,
    "logNameF4": LOG_NAME_F4,
}


def verify_graph_model(graph: Graph) -> list[dict[str, Any]]:
    """Check Q1-Q10 against actual nodes and relationship paths."""
    outgoing = {
        (edge.type, edge.start_key): edge.end_key
        for edge in graph.relationships.values()
    }

    def wellbores_for(well_id: str) -> list[str]:
        return sorted(
            node.properties["name"]
            for node in graph.nodes.values()
            if node.label == "Wellbore"
            and outgoing.get(("BELONGS_TO_WELL", node.key)) == well_id
        )

    def logs_for(wellbore_id: str) -> list[str]:
        return sorted(
            node.properties["name"]
            for node in graph.nodes.values()
            if node.label == "WellLog"
            and outgoing.get(("BELONGS_TO_WELLBORE", node.key)) == wellbore_id
        )

    def parent_of_log(log_name: str) -> dict[str, str | None]:
        target_log = next(
            (
                node
                for node in graph.nodes.values()
                if node.label == "WellLog" and node.properties.get("name") == log_name
            ),
            None,
        )
        parent_wellbore_id = (
            outgoing.get(("BELONGS_TO_WELLBORE", target_log.key))
            if target_log is not None
            else None
        )
        parent_well_id = (
            outgoing.get(("BELONGS_TO_WELL", parent_wellbore_id))
            if parent_wellbore_id is not None
            else None
        )
        return {
            "well": _name(graph, parent_well_id),
            "wellbore": _name(graph, parent_wellbore_id),
        }

    counts = [
        {"label": label, "count": count}
        for label, count in sorted(
            (
                ("Well", sum(1 for n in graph.nodes.values() if n.label == "Well")),
                (
                    "WellLog",
                    sum(1 for n in graph.nodes.values() if n.label == "WellLog"),
                ),
                (
                    "Wellbore",
                    sum(1 for n in graph.nodes.values() if n.label == "Wellbore"),
                ),
            ),
            key=lambda item: item[0],
        )
    ]

    actual_by_id = {
        "Q1": wellbores_for(WELL_ID),
        "Q2": logs_for(WELLBORE_ID),
        "Q3": parent_of_log(LOG_NAME),
        "Q4": WELLBORE_ID in graph.nodes,
        "Q5": WELL_ID in graph.nodes,
        "Q6": wellbores_for(WELL_ID_F1),
        "Q7": parent_of_log(LOG_NAME_F4),
        "Q8": WELLBORE_ID_A in graph.nodes,
        "Q9": wellbores_for(WELL_ID_F15),
        "Q10": counts,
    }
    expected_by_id = {
        "Q1": EXPECTED_WELLBORES,
        "Q2": EXPECTED_LOGS,
        "Q3": {"well": "15/9-19 S", "wellbore": "15/9-19 SR"},
        "Q4": True,
        "Q5": True,
        "Q6": EXPECTED_WELLBORES_F1,
        "Q7": {"well": "15/9-F-4", "wellbore": "15/9-F-4"},
        "Q8": True,
        "Q9": EXPECTED_WELLBORES_F15,
        "Q10": EXPECTED_COUNTS,
    }
    return [
        {
            "id": control.id,
            "question": control.question,
            "passed": actual_by_id[control.id] == expected_by_id[control.id],
            "expected": expected_by_id[control.id],
            "actual": actual_by_id[control.id],
        }
        for control in CONTROLS
    ]


def _name(graph: Graph, key: str | None) -> str | None:
    node = graph.nodes.get(key or "")
    value = node.properties.get("name") if node is not None else None
    return value if isinstance(value, str) else None
