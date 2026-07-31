"""bkg/ — Ejemplo de Knowledge Graph multi-dominio (Medical).

Demuestra cómo otro dominio (medicina) usa el mismo Graph base
que CKG (desarrollo), con sus propios tipos y metadata.

Concepto: el grafo es multi-dominio. No sabe de medicina ni de software.
Cada dominio define su vocabulario (tipos, metadata) sobre el mismo grafo.
"""
from __future__ import annotations
from ckg.domain.knowledge_graph import KnowledgeNode, KnowledgeEdge, KnowledgeGraph


# Vocabulario del dominio médico
class MedicalNodeType:
    PATIENT = "patient"
    DIAGNOSIS = "diagnosis"
    PROCEDURE = "procedure"
    MEDICATION = "medication"
    SYMPTOM = "symptom"
    PROTOCOL = "protocol"
    CLINICAL_STUDY = "clinical_study"


class MedicalEdgeType:
    PRESENTS = "PRESENTS"          # patient → symptom
    DIAGNOSED_WITH = "DIAGNOSED_WITH"
    TREATED_BY = "TREATED_BY"      # diagnosis → procedure/medication
    CONTRAINDICATES = "CONTRAINDICATES"
    FOLLOWS_PROTOCOL = "FOLLOWS_PROTOCOL"


class MedicalKnowledgeGraph:
    """Grafo de conocimiento médico. Usa el mismo Graph base que CKG."""

    def __init__(self, storage_path: str):
        self._graph = Graph()
        self._path = storage_path

    def add_patient(self, patient_id: str, age: int, blood_type: str) -> dict:
        node = Node(id=patient_id, type=MedicalNodeType.PATIENT, label=f"Patient {patient_id}",
                    metadata={"age": age, "blood_type": blood_type, "risk_factors": []})
        self._graph.add_node(node)
        return {"ok": True, "node_id": patient_id}

    def add_diagnosis(self, patient_id: str, diagnosis: str, confidence: float) -> dict:
        nid = f"diag-{self._graph.node_count + 1:03d}"
        self._graph.add_node(Node(id=nid, type=MedicalNodeType.DIAGNOSIS, label=diagnosis,
                                  metadata={"confidence": confidence, "patient_id": patient_id}))
        self._graph.add_edge(Edge(from_id=patient_id, to_id=nid, type=MedicalEdgeType.DIAGNOSED_WITH))
        return {"ok": True, "node_id": nid}

    def query_by_symptom(self, symptom: str) -> list[dict]:
        """Consulta pacientes con un síntoma específico."""
        return self._graph.query({"type": MedicalNodeType.SYMPTOM, "label": symptom})

    def save(self) -> None:
        self._graph.save_to_file(self._path)

    def load(self) -> None:
        if __import__("os").path.exists(self._path):
            self._graph = Graph.load_from_file(self._path)
