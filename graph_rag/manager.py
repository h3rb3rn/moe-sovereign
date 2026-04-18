"""
GraphRAG Manager — manages the Neo4j knowledge graph.

Responsibilities:
  - Build schema & indexes
  - Load base ontology
  - Retrieve context for incoming requests (query_context)
  - Extract and store entities & relations from LLM responses (extract_and_ingest)
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from neo4j import AsyncGraphDatabase, AsyncDriver

logger = logging.getLogger("MOE-SOVEREIGN.GraphRAG")

# Relation pairs that are logically contradictory (subject → rel → object).
# If (A)-[TREATS]->(B) exists and a new triple (A)-[CAUSES]->(B) arrives → conflict.
_CONTRADICTORY_PAIRS: Dict[str, List[str]] = {
    "TREATS":          ["CAUSES", "CONTRAINDICATES"],
    "CAUSES":          ["TREATS"],
    "CONTRAINDICATES": ["TREATS"],
    # Procedural relations have no strict logical inverses yet.
    "NECESSITATES_PRESENCE": [],
    "DEPENDS_ON_LOCATION":   [],
    "ENABLES_ACTION":        [],
}

# Procedural relation types extracted by the causal learning loop.
_PROCEDURAL_RELS = frozenset({"NECESSITATES_PRESENCE", "DEPENDS_ON_LOCATION", "ENABLES_ACTION"})

# ─── Trust Score & Confidence Decay ─────────────────────────────────────────
# Trust formula: confidence * source_weight * temporal_decay * verification_bonus
# Relations below _TRUST_DELETE_THRESHOLD that are unverified and single-assertion
# are candidates for automatic removal during graph linting.
_SOURCE_WEIGHTS: Dict[str, float] = {
    "ontology": 1.0,
    "ontology_gap_healer": 0.9,
    "extracted": 0.6,
}
_DECAY_PERIOD_DAYS = 365       # Full decay period (1 year)
_DECAY_FLOOR = 0.3             # Minimum temporal multiplier
_TRUST_DELETE_THRESHOLD = 0.2  # Trust below this = candidate for removal
_BLAST_RADIUS_THRESHOLD = 20   # Max connected entities before quarantine

# Domain filter: which entity types each category is allowed to see.
# None = no filter (all types permitted).
_CATEGORY_ENTITY_TYPES: Dict[str, Optional[set]] = {
    "medical_consult":  {"Drug", "Disease", "Symptom", "Treatment", "Anatomy", "Medical_Concept", "Drug_Class"},
    "legal_advisor":    {"Law", "Right", "Legal_Concept", "Organization"},
    "technical_support":{"Framework", "Tool", "Protocol", "Tech_Concept", "DevOps", "Architecture",
                         "Security", "DataStructure", "Algorithm", "Pattern", "Principle",
                         "Action", "Location", "Condition"},
    "code_reviewer":    {"Framework", "Tool", "Tech_Concept", "DataStructure", "Algorithm",
                         "Pattern", "Principle", "Architecture", "Security", "Game_Concept",
                         "Data_Concept", "Action", "Condition"},
    "math":             {"Math_Concept", "Science", "Concept", "Algorithm", "DataStructure"},
    "translation":      {"Language", "Concept", "Narrative"},
    "creative_writer":  {"Narrative", "Concept"},
    "reasoning":        None,
    "general":          None,
    "research":         None,
    "precision_tools":  None,
}

# Semaphore limiting concurrent LLM calls during background ingest.
# Created lazily inside the async event loop on first use.
_ingest_semaphore: Optional[asyncio.Semaphore] = None


def _get_ingest_semaphore() -> asyncio.Semaphore:
    """Returns the module-level ingest semaphore, creating it on first call."""
    global _ingest_semaphore
    if _ingest_semaphore is None:
        _ingest_semaphore = asyncio.Semaphore(2)
    return _ingest_semaphore

# Stopwords for term-based entity recognition (no extra LLM call)
_STOPWORDS = {
    "der", "die", "das", "ein", "eine", "ist", "sind", "hat", "haben", "war",
    "wird", "werden", "wurde", "können", "kann", "soll", "muss", "darf",
    "wie", "was", "wer", "wo", "wann", "warum", "welche", "welcher", "welches",
    "und", "oder", "aber", "auch", "nicht", "noch", "nur", "schon", "bereits",
    "in", "an", "auf", "bei", "mit", "von", "zu", "aus", "nach", "über",
    "mir", "ich", "du", "er", "sie", "es", "wir", "ihr", "man", "sich",
    "bitte", "erkläre", "erklärt", "beschreibe", "zeige", "nenne", "liste",
    "the", "a", "an", "is", "are", "was", "what", "how", "why", "and", "or",
    "tell", "me", "about", "explain", "describe", "please", "give",
}


class GraphRAGManager:

    def __init__(self, uri: str, user: str, password: str):
        self.driver: AsyncDriver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    # ─── SETUP ───────────────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Build schema, indexes and base ontology."""
        await self._create_schema()
        loaded = await self._load_ontology()
        stats = await self.get_stats()
        logger.info(
            f"✅ GraphRAG initialized — "
            f"{stats['entities']} entities, {stats['relations']} relations "
            f"({loaded} ontology triples loaded)"
        )

    async def _create_schema(self) -> None:
        async with self.driver.session() as session:
            # Constraint: Entity.name must be unique
            await session.run("""
                CREATE CONSTRAINT entity_name_unique IF NOT EXISTS
                FOR (e:Entity) REQUIRE e.name IS UNIQUE
            """)
            # Index on type for fast type filtering
            await session.run("""
                CREATE INDEX entity_type_idx IF NOT EXISTS
                FOR (e:Entity) ON (e.type)
            """)
            # Index on Synthesis.domain for domain-scoped queries
            await session.run("""
                CREATE INDEX synthesis_domain_idx IF NOT EXISTS
                FOR (s:Synthesis) ON (s.domain)
            """)
            # Index on Entity.tenant_id for multi-tenant RBAC filtering
            await session.run("""
                CREATE INDEX entity_tenant_idx IF NOT EXISTS
                FOR (e:Entity) ON (e.tenant_id)
            """)

    async def _load_ontology(self) -> int:
        """Loads the base ontology if not yet present. Returns count of new triples."""
        from .ontology import ONTOLOGY
        loaded = 0
        async with self.driver.session() as session:
            # Create entities (MERGE = idempotent)
            for ent in ONTOLOGY["entities"]:
                await session.run(
                    """
                    MERGE (e:Entity {name: $name})
                    ON CREATE SET
                        e.type        = $type,
                        e.aliases     = $aliases,
                        e.aliases_str = $aliases_str,
                        e.source      = 'ontology'
                    """,
                    {
                        "name": ent["name"],
                        "type": ent["type"],
                        "aliases": ent.get("aliases", []),
                        "aliases_str": " ".join(ent.get("aliases", [])),
                    },
                )

            # Create relations
            for rel in ONTOLOGY["relations"]:
                rel_type = re.sub(r"[^A-Z_]", "", rel["rel"].upper())
                if not rel_type:
                    continue
                result = await session.run(
                    f"""
                    MATCH (a:Entity {{name: $from_name}})
                    MATCH (b:Entity {{name: $to_name}})
                    MERGE (a)-[r:{rel_type} {{source: 'ontology'}}]->(b)
                    ON CREATE SET r.created = timestamp()
                    RETURN r
                    """,
                    {"from_name": rel["from"], "to_name": rel["to"]},
                )
                records = await result.data()
                if records:
                    loaded += 1
        return loaded

    # ─── QUERY ───────────────────────────────────────────────────────────────

    def _resolve_allowed_types(self, categories: List[str]) -> Optional[set]:
        """
        Computes the allowed entity types from a list of plan categories.
        - Categories mapped to None contribute nothing to the filter.
        - Categories with defined type sets are unioned together.
        - If the result is empty (only None-mapped categories) → no filter (all types allowed).
        - If at least one category has a type set → only those types are allowed.
        """
        if not categories:
            return None
        allowed: set = set()
        has_defined = False
        for cat in categories:
            types = _CATEGORY_ENTITY_TYPES.get(cat)
            if types is not None:
                allowed |= types
                has_defined = True
            # None-mapped categories (general, reasoning, precision_tools, …) contribute nothing
        return allowed if has_defined else None

    async def query_context(
        self,
        query_text: str,
        categories: Optional[List[str]] = None,
        max_hops: int = 2,
        tenant_ids: Optional[List[str]] = None,
    ) -> str:
        """
        Returns structured graph context for a request.
        Filters entity types based on plan categories to prevent domain cross-contamination.
        Optionally filters by tenant_ids for multi-tenant RBAC isolation.
        Searches entities in text → traverses 1-2 hops → formats as text.
        Shows provenance metadata (source, confidence) for temporal traceability.
        """
        terms = self._extract_terms(query_text)
        if not terms:
            return ""

        allowed_types = self._resolve_allowed_types(categories or [])
        type_filter = "AND e.type IN $allowed_types" if allowed_types else ""
        # Tenant RBAC: if tenant_ids provided, only show entities belonging to
        # those tenants or entities with no tenant (shared/public).
        tenant_filter = (
            "AND (e.tenant_id IN $tenant_ids OR e.tenant_id IS NULL)"
            if tenant_ids else ""
        )
        if allowed_types:
            logger.debug(f"GraphRAG domain filter active: {allowed_types}")
        if tenant_ids:
            logger.debug(f"GraphRAG tenant filter active: {tenant_ids}")

        found: Dict[str, Any] = {}

        async with self.driver.session() as session:
            for term in terms[:3]:
                result = await session.run(
                    f"""
                    MATCH (e:Entity)
                    WHERE (toLower(e.name) CONTAINS toLower($term)
                        OR toLower(e.aliases_str) CONTAINS toLower($term))
                    {type_filter}
                    {tenant_filter}
                    WITH e LIMIT 1
                    OPTIONAL MATCH (e)-[r1]->(n1:Entity)
                    OPTIONAL MATCH (n1)-[r2]->(n2:Entity)
                    RETURN
                        e.name      AS root,
                        e.type      AS root_type,
                        collect(DISTINCT {{
                            rel:          type(r1),
                            target:       n1.name,
                            ttype:        n1.type,
                            source_model: r1.source_model,
                            confidence:   r1.confidence,
                            version:      r1.version
                        }})[..6] AS direct,
                        collect(DISTINCT {{
                            via:    n1.name,
                            rel:    type(r2),
                            target: n2.name,
                            ttype:  n2.type
                        }})[..4] AS indirect
                    """,
                    {
                        "term": term,
                        "allowed_types": list(allowed_types) if allowed_types else [],
                        "tenant_ids": tenant_ids or [],
                    },
                )
                async for record in result:
                    root = record["root"]
                    if not root or root in found:
                        continue
                    found[root] = {
                        "type":     record["root_type"],
                        "direct":   [r for r in record["direct"]   if r["target"]],
                        "indirect": [r for r in record["indirect"]  if r["target"]],
                    }

        if not found:
            return ""

        lines = ["[Knowledge Graph]"]
        for entity, data in found.items():
            etype = data["type"]
            rels = data["direct"]
            indirect = data["indirect"]

            if rels:
                rel_parts = []
                for r in rels[:4]:
                    part = f"{r['rel']} {r['target']}"
                    conf = r.get("confidence")
                    src  = r.get("source_model")
                    ver  = r.get("version")
                    # Add provenance hint for low confidence or updated relations
                    if conf is not None and conf < 0.4:
                        part += f" [confidence: {conf:.0%}, source: {src or '?'}]"
                    elif ver is not None and ver > 1:
                        part += f" [v{ver}, source: {src or '?'}]"
                    rel_parts.append(part)
                lines.append(f"• {entity} ({etype}): {' | '.join(rel_parts)}")
            else:
                lines.append(f"• {entity} ({etype})")

            if indirect:
                ind_parts = [
                    f"{r['via']} → {r['rel']} → {r['target']}"
                    for r in indirect[:2]
                    if r["target"]
                ]
                if ind_parts:
                    lines.append(f"  ↳ {' | '.join(ind_parts)}")

        # Targeted procedural traversal: look up requirements for any Action entities found.
        action_names = [e for e, d in found.items() if d.get("type") == "Action"]
        if action_names:
            proc_lines = await self._query_procedural_requirements(action_names)
            if proc_lines:
                lines.append("")
                lines.append("[Procedural Requirements]")
                lines.extend(proc_lines)

        return "\n".join(lines)

    async def _query_procedural_requirements(self, action_names: List[str]) -> List[str]:
        """
        For a list of Action-type entities, fetches all procedural dependencies
        via NECESSITATES_PRESENCE, DEPENDS_ON_LOCATION, and ENABLES_ACTION relations.
        Returns formatted lines ready to append to the graph context block.
        """
        lines: List[str] = []
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (a:Entity)
                WHERE a.name IN $action_names AND a.type = 'Action'
                MATCH (a)-[r:NECESSITATES_PRESENCE|DEPENDS_ON_LOCATION]->(dep:Entity)
                RETURN a.name AS action, type(r) AS rel, dep.name AS dependency,
                       dep.type AS dep_type
                UNION
                MATCH (cond:Entity)-[r:ENABLES_ACTION]->(a:Entity)
                WHERE a.name IN $action_names AND a.type = 'Action'
                RETURN a.name AS action, 'ENABLED_BY' AS rel, cond.name AS dependency,
                       cond.type AS dep_type
                LIMIT 20
                """,
                {"action_names": action_names},
            )
            async for record in result:
                lines.append(
                    f"• {record['action']} {record['rel']} {record['dependency']} "
                    f"({record['dep_type']})"
                )
        return lines

    def _extract_terms(self, text: str) -> List[str]:
        """
        Extracts relevant search terms from the query text without an LLM call.
        Prefers capitalized words (nouns/proper nouns) and longer terms.
        """
        # Capitalized words first (nouns, proper names)
        caps = re.findall(r"\b[A-ZÄÖÜ][a-zäöüßA-ZÄÖÜ0-9+#]{2,}\b", text)
        # All words >= 4 characters
        words = re.findall(r"\b[A-Za-zÄäÖöÜüß]{4,}\b", text)
        seen: set = set()
        result = []
        for w in caps + words:
            key = w.lower()
            if key not in _STOPWORDS and key not in seen:
                seen.add(key)
                result.append(w)
        return result[:10]

    # ─── INGEST ──────────────────────────────────────────────────────────────

    async def _check_conflict(
        self, session: Any, s_name: str, rel_type: str, o_name: str
    ) -> Optional[str]:
        """
        Checks whether a new triple contradicts an existing one.
        Returns conflict description or None if no conflict.
        """
        contradictory = _CONTRADICTORY_PAIRS.get(rel_type, [])
        if not contradictory:
            return None
        for contra in contradictory:
            result = await session.run(
                f"MATCH (a:Entity {{name: $s}})-[:{contra}]->(b:Entity {{name: $o}}) "
                "RETURN count(*) AS cnt",
                {"s": s_name, "o": o_name},
            )
            record = await result.single()
            if record and record["cnt"] > 0:
                return f"({s_name})-[{contra}]->({o_name}) exists, conflicts with [{rel_type}]"
        return None

    async def extract_and_ingest(
        self,
        question: str,
        answer: str,
        llm: Any,
        domain: Optional[str] = None,
        source_model: str = "unknown",
        confidence: float = 0.5,
        knowledge_type: str = "factual",
        expert_domain: str = "",
        tenant_id: Optional[str] = None,
        redis_client: Any = None,
    ) -> str:
        """
        Extracts entities and relations from an LLM response via a judge LLM
        and stores them in the graph. Called as a background task (fire & forget).
        Provenance metadata (source_model, confidence, valid_from, version) is
        stored on each relation for temporal traceability.

        Returns the detected knowledge_type ('factual' or 'procedural') so callers
        can log or act on the classification.
        """
        extract_prompt = (
            "Extract entities and relationships from the following text as a compact JSON array.\n"
            "Only factually confirmed statements! Maximum 8 triples.\n\n"
            "Allowed relation types:\n"
            "  IS_A, PART_OF, TREATS, CAUSES, INTERACTS_WITH, CONTRAINDICATES,\n"
            "  DEFINES, REGULATES, USES, IMPLEMENTS, DEPENDS_ON, EXTENDS,\n"
            "  RELATED_TO, EQUIVALENT_TO, AFFECTS, RUNS,\n"
            "  NECESSITATES_PRESENCE, DEPENDS_ON_LOCATION, ENABLES_ACTION\n\n"
            "Allowed entity types:\n"
            "  Drug, Disease, Symptom, Treatment, Anatomy, Medical_Concept,\n"
            "  Law, Right, Legal_Concept, Language, Framework, Tool, Protocol,\n"
            "  AI_Concept, Tech_Concept, Math_Concept, Science, Concept, Person,\n"
            "  Organization, Action, Location, Condition\n\n"
            "IMPORTANT — also extract procedural world-rules:\n"
            "  If the text implies that performing an action requires physical presence\n"
            "  at a location, use NECESSITATES_PRESENCE (Action → Location).\n"
            "  If a prerequisite condition or resource enables an action, use\n"
            "  ENABLES_ACTION (Condition → Action). Maximum 4 procedural triples.\n\n"
            'Format: [{"s":"Name","s_type":"Type","r":"RELATION","o":"Name","o_type":"Type"}]\n\n'
            f"Text:\n{answer[:1800]}\n\n"
            "JSON array (ONLY the array, no explanatory text):"
        )

        try:
            async with _get_ingest_semaphore():
                result = await llm.ainvoke(extract_prompt)
            match = re.search(r"\[.*?\]", result.content, re.S)
            if not match:
                return

            triples = json.loads(match.group())
            if not triples or not isinstance(triples, list):
                return

            # Auto-detect knowledge_type from extracted triples
            detected_type = (
                "procedural"
                if any(t.get("r", "").upper() in _PROCEDURAL_RELS for t in triples)
                else knowledge_type
            )

            ingested = 0
            conflicts = 0
            async with self.driver.session() as session:
                for triple in triples[:8]:
                    if not all(k in triple for k in ("s", "s_type", "r", "o", "o_type")):
                        continue
                    rel_type = re.sub(r"[^A-Z_]", "", str(triple["r"]).upper())
                    if not rel_type:
                        continue
                    s_name = str(triple["s"])[:100].strip()
                    o_name = str(triple["o"])[:100].strip()
                    if not s_name or not o_name:
                        continue

                    # Conflict check before saving
                    conflict = await self._check_conflict(session, s_name, rel_type, o_name)
                    if conflict:
                        logger.warning(f"⚠️ GraphRAG conflict skipped: {conflict}")
                        conflicts += 1
                        continue

                    # Blast-radius check: quarantine high-impact triples
                    if redis_client:
                        reach = await self._estimate_blast_radius(session, s_name, o_name)
                        if reach > _BLAST_RADIUS_THRESHOLD:
                            q_entry = json.dumps({
                                "s": s_name, "s_type": str(triple["s_type"]),
                                "r": rel_type,
                                "o": o_name, "o_type": str(triple["o_type"]),
                                "reach": reach, "question": question[:200],
                                "domain": domain, "source_model": source_model,
                                "confidence": confidence,
                                "tenant_id": tenant_id,
                                "ts": datetime.now(timezone.utc).isoformat(),
                            })
                            try:
                                await redis_client.zadd("moe:quarantine", {q_entry: time.time()})
                                await redis_client.expire("moe:quarantine", 7 * 86400)
                            except Exception:
                                pass
                            logger.warning(
                                f"⚠️ GraphRAG quarantined: ({s_name})-[{rel_type}]->({o_name}) "
                                f"reach={reach} > {_BLAST_RADIUS_THRESHOLD}"
                            )
                            continue

                    await session.run(
                        f"""
                        MERGE (a:Entity {{name: $s_name}})
                        ON CREATE SET a.type = $s_type, a.source = 'extracted',
                                      a.domain = $domain, a.expert_domain = $expert_domain,
                                      a.tenant_id = $tenant_id
                        MERGE (b:Entity {{name: $o_name}})
                        ON CREATE SET b.type = $o_type, b.source = 'extracted',
                                      b.domain = $domain, b.expert_domain = $expert_domain,
                                      b.tenant_id = $tenant_id
                        MERGE (a)-[r:{rel_type} {{source: 'extracted'}}]->(b)
                        ON CREATE SET
                            r.created       = timestamp(),
                            r.valid_from    = timestamp(),
                            r.from_q        = $question,
                            r.verified      = false,
                            r.domain        = $domain,
                            r.expert_domain = $expert_domain,
                            r.source_model  = $source_model,
                            r.confidence    = $confidence,
                            r.version       = 1
                        ON MATCH SET
                            r.prev_source_model  = r.source_model,
                            r.prev_confidence    = r.confidence,
                            r.superseded_version = r.version,
                            r.version            = coalesce(r.version, 0) + 1,
                            r.source_model       = $source_model,
                            r.confidence         = $confidence,
                            r.valid_from         = timestamp()
                        """,
                        {
                            "s_name":        s_name,
                            "s_type":        str(triple["s_type"])[:50],
                            "o_name":        o_name,
                            "o_type":        str(triple["o_type"])[:50],
                            "question":      question[:200],
                            "domain":        domain or "unknown",
                            "expert_domain": expert_domain,
                            "source_model":  source_model,
                            "confidence":    confidence,
                            "tenant_id":     tenant_id,
                        },
                    )
                    ingested += 1

            if ingested or conflicts:
                logger.info(
                    f"GraphRAG ingest: {ingested} saved, {conflicts} conflicts skipped "
                    f"[{detected_type}]"
                )
            return detected_type

        except json.JSONDecodeError:
            logger.debug("GraphRAG ingest: no valid JSON extracted")
        except Exception as e:
            logger.warning(f"GraphRAG ingest error: {e}")
        return knowledge_type

    # ─── STATS & SEARCH ──────────────────────────────────────────────────────

    async def get_stats(self) -> Dict[str, int]:
        """Returns entity, relation, synthesis node, and flagged relation counts."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (e:Entity)   WITH count(e) AS entities
                MATCH ()-[r]->()   WITH entities, count(r) AS relations
                OPTIONAL MATCH (s:Synthesis) WITH entities, relations, count(s) AS synthesis_nodes
                OPTIONAL MATCH ()-[fr {flagged: true}]->()
                RETURN entities, relations, synthesis_nodes,
                       count(fr) AS flagged_relations
            """)
            record = await result.single()
            if record:
                return {
                    "entities":          record["entities"],
                    "relations":         record["relations"],
                    "synthesis_nodes":   record["synthesis_nodes"] or 0,
                    "flagged_relations": record["flagged_relations"] or 0,
                }
        return {"entities": 0, "relations": 0, "synthesis_nodes": 0, "flagged_relations": 0}

    async def search_entities(self, term: str, limit: int = 10) -> List[Dict]:
        """Simple entity search — useful for debugging & admin."""
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (e:Entity)
                WHERE toLower(e.name) CONTAINS toLower($term)
                   OR toLower(e.aliases_str) CONTAINS toLower($term)
                RETURN e.name AS name, e.type AS type, e.source AS source
                LIMIT $limit
                """,
                {"term": term, "limit": limit},
            )
            return [dict(r) async for r in result]

    async def get_provenance(self, entity_name: str) -> List[Dict]:
        """
        Returns all relations of an entity with complete version history.
        Useful for debugging, admin UI and contradiction analysis.
        """
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (a:Entity {name: $name})-[r]->(b:Entity)
                RETURN
                    type(r)            AS relation,
                    b.name             AS target,
                    r.source_model     AS source_model,
                    r.confidence       AS confidence,
                    r.version          AS version,
                    r.valid_from       AS valid_from,
                    r.prev_source_model AS prev_source_model,
                    r.prev_confidence   AS prev_confidence,
                    r.superseded_version AS superseded_version,
                    r.verified         AS verified,
                    r.flagged          AS flagged,
                    r.domain           AS domain
                ORDER BY r.valid_from DESC
                """,
                {"name": entity_name},
            )
            return [dict(r) async for r in result]

    async def mark_triples_unverified(self, question: str) -> int:
        """Marks all triples extracted from a question as unverified and flagged (negative feedback)."""
        async with self.driver.session() as session:
            result = await session.run(
                "MATCH ()-[r {from_q: $q}]->() "
                "SET r.verified = false, r.flagged = true "
                "RETURN count(r) AS cnt",
                {"q": question[:200]},
            )
            record = await result.single()
            return record["cnt"] if record else 0

    async def verify_triples(self, question: str) -> int:
        """Marks all triples extracted from a question as verified (positive feedback)."""
        async with self.driver.session() as session:
            result = await session.run(
                "MATCH ()-[r {from_q: $q}]->() "
                "SET r.verified = true, r.flagged = false "
                "RETURN count(r) AS cnt",
                {"q": question[:200]},
            )
            record = await result.single()
            return record["cnt"] if record else 0

    # ─── GRAPH LINTING ───────────────────────────────────────────────────────

    # ─── TRUST SCORE & SELF-HEALING ────────────────────────────────────────────

    async def compute_trust_scores(self, batch_size: int = 500) -> int:
        """
        Recomputes trust_score on all relations.
        Formula: trust = confidence * source_weight * temporal_decay * verification_bonus
        Returns count of relations updated.
        """
        updated = 0
        now_ts = time.time() * 1000  # Neo4j timestamp() is milliseconds
        try:
            async with self.driver.session() as session:
                result = await session.run("""
                    MATCH ()-[r]->()
                    RETURN id(r) AS rid,
                           r.confidence AS conf,
                           r.source AS src,
                           r.valid_from AS vf,
                           r.verified AS verified
                    LIMIT $batch
                """, {"batch": batch_size})
                rows = [dict(r) async for r in result]

            for row in rows:
                conf = row.get("conf") or 0.5
                src = row.get("src") or "extracted"
                vf = row.get("vf") or now_ts
                verified = bool(row.get("verified"))

                source_w = _SOURCE_WEIGHTS.get(src, 0.6)
                days_old = max(0, (now_ts - vf) / (86400 * 1000))
                decay = max(_DECAY_FLOOR, 1.0 - (days_old / _DECAY_PERIOD_DAYS))
                v_bonus = 1.5 if verified else 1.0
                trust = min(1.0, conf * source_w * decay * v_bonus)

                async with self.driver.session() as session:
                    await session.run(
                        "MATCH ()-[r]->() WHERE id(r) = $rid SET r.trust_score = $score",
                        {"rid": row["rid"], "score": round(trust, 4)},
                    )
                updated += 1

            logger.info(f"Trust scores recomputed for {updated} relations")
        except Exception as exc:
            logger.warning(f"Trust score computation failed: {exc}")
        return updated

    async def _estimate_blast_radius(
        self, session: Any, s_name: str, o_name: str
    ) -> int:
        """
        Counts distinct entities reachable within 2 hops from subject OR object.
        Used to decide whether a new triple should be quarantined.
        """
        try:
            result = await session.run("""
                OPTIONAL MATCH (a:Entity {name: $s})-[*1..2]-(n1:Entity)
                WITH collect(DISTINCT n1.name) AS s_neighbors
                OPTIONAL MATCH (b:Entity {name: $o})-[*1..2]-(n2:Entity)
                WITH s_neighbors + collect(DISTINCT n2.name) AS all_n
                UNWIND all_n AS n
                RETURN count(DISTINCT n) AS reach
            """, {"s": s_name, "o": o_name})
            record = await result.single()
            return record["reach"] if record else 0
        except Exception:
            return 0

    async def run_graph_linting(
        self, llm: Any, kafka_publish_fn: Optional[Callable] = None,
    ) -> Dict[str, int]:
        """
        Background janitor: removes orphaned nodes, resolves contradictions, and
        sweeps decayed low-trust relations.

        Phase 1: Delete Entity nodes that have no relationships (orphans), up to 50 per run.
        Phase 2: For each unique contradictory relationship pair in _CONTRADICTORY_PAIRS,
                 find same-subject conflicts, ask the LLM which to keep, then flag the loser.
        Phase 3: Confidence decay sweep — recompute trust scores and delete relations
                 with trust < 0.2 that are unverified and never re-confirmed (version=1).
        Throttled via _ingest_semaphore to avoid VRAM contention with active requests.

        Returns:
            dict with 'orphans_deleted', 'conflicts_resolved', and 'decay_deleted' counts.
        """
        logger.info("🧹 Graph-Linting: starting")

        # ── Phase 1: Orphan cleanup ─────────────────────────────────────────
        orphans_deleted = 0
        try:
            async with self.driver.session() as session:
                result = await session.run("""
                    MATCH (e:Entity)
                    WHERE NOT (e)--()
                    WITH e LIMIT 50
                    DELETE e
                    RETURN count(e) AS deleted
                """)
                record = await result.single()
                orphans_deleted = record["deleted"] if record else 0
            if orphans_deleted:
                logger.info(f"🧹 Graph-Linting orphans: {orphans_deleted} nodes deleted")
        except Exception as exc:
            logger.warning(f"Graph-Linting orphan phase failed: {exc}")

        # ── Phase 2: Conflict detection & LLM resolution ────────────────────
        seen_pairs: set = set()
        conflicts_resolved = 0

        for rel_a, contradicts in _CONTRADICTORY_PAIRS.items():
            for rel_b in contradicts:
                # Deduplicate symmetric pairs (TREATS/CAUSES == CAUSES/TREATS)
                pair_key = frozenset([rel_a, rel_b])
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                try:
                    async with self.driver.session() as session:
                        result = await session.run(
                            f"""
                            MATCH (a:Entity)-[r1:{rel_a}]->(x:Entity)
                            WHERE (a)-[:{rel_b}]->(x)
                              AND (r1.flagged IS NULL OR r1.flagged = false)
                            MATCH (a)-[r2:{rel_b}]->(x)
                            WHERE (r2.flagged IS NULL OR r2.flagged = false)
                            RETURN a.name      AS subject,
                                   x.name      AS target,
                                   r1.confidence AS conf_a,
                                   r2.confidence AS conf_b,
                                   r1.source_model AS model_a,
                                   r2.source_model AS model_b
                            LIMIT 10
                            """
                        )
                        rows = [dict(r) async for r in result]
                except Exception as exc:
                    logger.warning(f"Graph-Linting conflict query [{rel_a}/{rel_b}] failed: {exc}")
                    continue

                for row in rows:
                    subject = row["subject"]
                    target  = row["target"]
                    conf_a  = row.get("conf_a", 0.5)
                    conf_b  = row.get("conf_b", 0.5)
                    model_a = row.get("model_a", "unknown")
                    model_b = row.get("model_b", "unknown")

                    prompt = (
                        f"Two contradictory facts are stored about the same entity:\n"
                        f"  (1) ({subject})-[{rel_a}]->({target})  "
                        f"[confidence={conf_a}, model={model_a}]\n"
                        f"  (2) ({subject})-[{rel_b}]->({target})  "
                        f"[confidence={conf_b}, model={model_b}]\n"
                        f"Which relationship is more likely correct? "
                        f'Respond ONLY with JSON: {{"keep": "{rel_a}" or "{rel_b}", '
                        f'"reason": "one sentence explanation"}}'
                    )

                    try:
                        async with _get_ingest_semaphore():
                            response = await llm.ainvoke(prompt)
                        verdict  = json.loads(response.content.strip())
                        keep     = str(verdict.get("keep", "")).upper()
                        reason   = str(verdict.get("reason", ""))[:200]
                        flag_rel = rel_b if keep == rel_a else rel_a
                        if keep not in (rel_a, rel_b):
                            logger.debug(f"Graph-Linting: unrecognized keep='{keep}', skipping")
                            await asyncio.sleep(0.5)
                            continue
                    except Exception as exc:
                        logger.warning(
                            f"Graph-Linting LLM verdict failed for ({subject},{target}): {exc}"
                        )
                        await asyncio.sleep(0.5)
                        continue

                    # Write-back: flag the losing relationship
                    try:
                        async with self.driver.session() as session:
                            await session.run(
                                f"""
                                MATCH (a:Entity {{name: $subject}})-[r:{flag_rel}]->
                                      (x:Entity {{name: $target}})
                                SET r.flagged    = true,
                                    r.lint_note  = $reason,
                                    r.lint_ts    = timestamp(),
                                    r.lint_model = $model
                                """,
                                {
                                    "subject": subject,
                                    "target":  target,
                                    "reason":  reason,
                                    "model":   getattr(llm, "model_name", "unknown"),
                                },
                            )
                        conflicts_resolved += 1
                        logger.debug(
                            f"🧹 Linting: flagged ({subject})-[{flag_rel}]->({target}) — {reason}"
                        )
                    except Exception as exc:
                        logger.warning(f"Graph-Linting write-back failed: {exc}")

                    # Yield the event loop between LLM calls to avoid VRAM starvation
                    await asyncio.sleep(0.5)

        logger.info(f"🧹 Graph-Linting Phase 2 complete: {conflicts_resolved} conflicts resolved")

        # ── Phase 3: Confidence decay sweep ────────────────────────────────
        decay_deleted = 0
        try:
            await self.compute_trust_scores(batch_size=500)
            async with self.driver.session() as session:
                result = await session.run("""
                    MATCH (a:Entity)-[r]->(b:Entity)
                    WHERE r.trust_score < $threshold
                      AND (r.verified IS NULL OR r.verified = false)
                      AND (r.version IS NULL OR r.version = 1)
                    RETURN id(r) AS rid, a.name AS subject, type(r) AS rel,
                           b.name AS target, r.trust_score AS score
                    LIMIT 100
                """, {"threshold": _TRUST_DELETE_THRESHOLD})
                stale = [dict(r) async for r in result]

            for row in stale:
                try:
                    async with self.driver.session() as session:
                        await session.run(
                            "MATCH ()-[r]->() WHERE id(r) = $rid DELETE r",
                            {"rid": row["rid"]},
                        )
                    decay_deleted += 1
                    logger.debug(
                        f"🧹 Decay: deleted ({row['subject']})-[{row['rel']}]->({row['target']}) "
                        f"trust={row['score']}"
                    )
                    # Publish to Kafka audit topic if available
                    if kafka_publish_fn:
                        try:
                            await kafka_publish_fn("moe.audit", {
                                "event": "confidence_decay_delete",
                                "subject": row["subject"],
                                "relation": row["rel"],
                                "target": row["target"],
                                "trust_score": row["score"],
                                "ts": datetime.now(timezone.utc).isoformat(),
                            })
                        except Exception:
                            pass  # Audit logging is best-effort
                except Exception as exc:
                    logger.warning(f"Decay delete failed for rid={row['rid']}: {exc}")

            if decay_deleted:
                logger.info(f"🧹 Graph-Linting Phase 3: {decay_deleted} decayed relations deleted")
        except Exception as exc:
            logger.warning(f"Graph-Linting decay phase failed: {exc}")

        logger.info(
            f"🧹 Graph-Linting complete: orphans={orphans_deleted}, "
            f"conflicts={conflicts_resolved}, decay={decay_deleted}"
        )
        return {
            "orphans_deleted": orphans_deleted,
            "conflicts_resolved": conflicts_resolved,
            "decay_deleted": decay_deleted,
        }

    # ─── SYNTHESIS PERSISTENCE ───────────────────────────────────────────────

    async def ingest_synthesis(
        self,
        synthesis: Dict[str, Any],
        domain: Optional[str] = None,
        source_model: str = "unknown",
        confidence: float = 0.5,
        expert_domain: str = "",
    ) -> None:
        """
        Persists a synthesis insight as a :Synthesis node linked to relevant :Entity nodes.

        Args:
            synthesis: Dict with keys:
                - "summary": str — the insight text (required)
                - "entities": List[str] — entity names to link via :RELATED_TO
                - "insight_type": str — "comparison" | "synthesis" | "inference"
            domain: Knowledge domain (propagated from the originating response).
            source_model: Model that produced the insight.
            confidence: Confidence score of the originating response.
        """
        summary      = str(synthesis.get("summary", "")).strip()[:500]
        insight_type = str(synthesis.get("insight_type", "synthesis"))[:50]
        entity_names = [
            str(e).strip()[:100]
            for e in synthesis.get("entities", [])
            if str(e).strip()
        ]

        if not summary:
            logger.debug("ingest_synthesis: empty summary, skipping")
            return

        # Deterministic ID: first 16 hex chars of sha256(summary)
        node_id = hashlib.sha256(summary.encode()).hexdigest()[:16]

        # Fallback: derive entity names from summary text if none were provided
        if not entity_names:
            entity_names = list(self._extract_terms(summary))[:10]

        try:
            async with self.driver.session() as session:
                # Create or update the :Synthesis node (idempotent via MERGE on id)
                await session.run(
                    """
                    MERGE (s:Synthesis {id: $id})
                    ON CREATE SET
                        s.text          = $summary,
                        s.insight_type  = $insight_type,
                        s.entities      = $entity_names,
                        s.created       = timestamp(),
                        s.domain        = $domain,
                        s.expert_domain = $expert_domain,
                        s.source_model  = $source_model,
                        s.confidence    = $confidence
                    """,
                    {
                        "id":            node_id,
                        "summary":       summary,
                        "insight_type":  insight_type,
                        "entity_names":  entity_names,
                        "domain":        domain or "unknown",
                        "expert_domain": expert_domain,
                        "source_model":  source_model,
                        "confidence":    confidence,
                    },
                )
                # Link :Synthesis to matching :Entity nodes in one batch query
                if entity_names:
                    await session.run(
                        """
                        MATCH (s:Synthesis {id: $id})
                        UNWIND $entity_names AS ename
                        MATCH (e:Entity {name: ename})
                        MERGE (s)-[:RELATED_TO]->(e)
                        """,
                        {"id": node_id, "entity_names": entity_names},
                    )
            logger.info(
                f"💡 Synthesis persisted: [{insight_type}] '{summary[:60]}...' "
                f"→ {len(entity_names)} entities linked"
            )
            return insight_type
        except Exception as exc:
            logger.warning(f"ingest_synthesis failed: {exc}")
            return None

    # ─── KNOWLEDGE EXPORT / IMPORT ──────────────────────────────────────────

    # ─── Privacy Scrubber (Semantic Leakage Protection) ────────────────────

    # Regex patterns that indicate potentially sensitive content in entity names
    _SENSITIVE_PATTERNS = [
        re.compile(r"password|passwd|secret|token|api.?key|credential", re.IGNORECASE),
        re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),  # IP addresses
        re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}"),  # Email
        re.compile(r"sk-[a-zA-Z0-9]{20,}|moe-sk-[a-f0-9]+"),  # API keys
        re.compile(r"prod_|staging_|internal_", re.IGNORECASE),  # Infra hints
        re.compile(r"kunde|client|customer.*[A-Z]", re.IGNORECASE),  # Client names
    ]

    def _is_sensitive_name(self, name: str) -> bool:
        """Check if an entity name contains potentially sensitive data."""
        return any(p.search(name) for p in self._SENSITIVE_PATTERNS)

    def _scrub_triple(self, subject: str, predicate: str, obj: str) -> bool:
        """Returns True if this triple should be EXCLUDED from export."""
        # Hard filter: known sensitive relation types
        sensitive_rels = {"HAS_PASSWORD", "HAS_CREDENTIAL", "HAS_TOKEN",
                          "AUTHENTICATES_WITH", "CONNECTS_TO_INTERNAL"}
        if predicate.upper() in sensitive_rels:
            return True
        # Check subject and object for PII/secrets
        if self._is_sensitive_name(subject) or self._is_sensitive_name(obj):
            return True
        return False

    async def export_knowledge_bundle(
        self,
        domains: Optional[List[str]] = None,
        min_trust: float = 0.3,
        include_syntheses: bool = True,
        strip_sensitive: bool = True,
    ) -> dict:
        """Export knowledge triples as a community-shareable JSON-LD bundle.

        Filters:
          - domains: Only export entities/relations in these expert domains (None = all)
          - min_trust: Minimum trust_score on relations (filters low-quality)
          - strip_sensitive: Remove tenant_id, source_model + run privacy scrubber
        Returns a dict ready for json.dumps().
        """
        bundle = {
            "@context": "https://moe-sovereign.org/knowledge/v1",
            "format_version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "filters": {
                "domains": domains,
                "min_trust": min_trust,
            },
            "entities": [],
            "relations": [],
            "syntheses": [],
            "stats": {},
        }

        async with self.driver.session() as session:
            # Export entities
            domain_filter = "AND e.domain IN $domains" if domains else ""
            q_entities = f"""
                MATCH (e:Entity)
                WHERE e.source IN ['ontology', 'extracted', 'ontology_gap_healer']
                {domain_filter}
                RETURN e.name AS name, e.type AS type, e.source AS source,
                       e.aliases AS aliases, e.domain AS domain,
                       e.tenant_id AS tenant_id
                ORDER BY e.name
            """
            params = {"domains": domains} if domains else {}
            result = await session.run(q_entities, params)
            records = await result.data()
            for r in records:
                entity = {
                    "name": r["name"],
                    "type": r["type"],
                    "source": r["source"],
                    "domain": r.get("domain"),
                }
                if r.get("aliases"):
                    entity["aliases"] = r["aliases"]
                if not strip_sensitive:
                    entity["tenant_id"] = r.get("tenant_id")
                # Privacy scrubber: skip entities with sensitive names
                if strip_sensitive and self._is_sensitive_name(r["name"]):
                    bundle.setdefault("scrubbed_count", 0)
                    bundle["scrubbed_count"] = bundle.get("scrubbed_count", 0) + 1
                    continue
                bundle["entities"].append(entity)

            # Export relations with trust filtering
            trust_filter = "AND r.trust_score >= $min_trust" if min_trust > 0 else ""
            q_relations = f"""
                MATCH (s:Entity)-[r]->(o:Entity)
                WHERE type(r) <> 'RELATED_TO_SYNTHESIS'
                {trust_filter}
                {domain_filter.replace('e.domain', 'r.domain')}
                RETURN s.name AS subject, type(r) AS predicate, o.name AS object,
                       r.confidence AS confidence, r.trust_score AS trust_score,
                       r.source AS source, r.domain AS domain,
                       r.verified AS verified, r.source_model AS source_model,
                       r.created AS created
                ORDER BY r.trust_score DESC
            """
            params["min_trust"] = min_trust
            result = await session.run(q_relations, params)
            records = await result.data()
            for r in records:
                rel = {
                    "subject": r["subject"],
                    "predicate": r["predicate"],
                    "object": r["object"],
                    "confidence": r.get("confidence"),
                    "trust_score": r.get("trust_score"),
                    "source": r.get("source"),
                    "domain": r.get("domain"),
                    "verified": r.get("verified", False),
                }
                if not strip_sensitive:
                    rel["source_model"] = r.get("source_model")
                    rel["created"] = str(r.get("created")) if r.get("created") else None
                # Privacy scrubber: skip triples with sensitive content
                if strip_sensitive and self._scrub_triple(r["subject"], r["predicate"], r["object"]):
                    bundle["scrubbed_count"] = bundle.get("scrubbed_count", 0) + 1
                    continue
                bundle["relations"].append(rel)

            # Export syntheses
            if include_syntheses:
                q_synth = f"""
                    MATCH (s:Synthesis)
                    WHERE s.confidence >= $min_trust
                    {domain_filter.replace('e.domain', 's.domain')}
                    RETURN s.id AS id, s.text AS text, s.insight_type AS insight_type,
                           s.entities AS entities, s.domain AS domain,
                           s.confidence AS confidence, s.source_model AS source_model
                    ORDER BY s.confidence DESC
                """
                result = await session.run(q_synth, params)
                records = await result.data()
                for r in records:
                    synth = {
                        "text": r["text"],
                        "insight_type": r.get("insight_type"),
                        "entities": r.get("entities", []),
                        "domain": r.get("domain"),
                        "confidence": r.get("confidence"),
                    }
                    if not strip_sensitive:
                        synth["source_model"] = r.get("source_model")
                    bundle["syntheses"].append(synth)

        bundle["stats"] = {
            "entities": len(bundle["entities"]),
            "relations": len(bundle["relations"]),
            "syntheses": len(bundle["syntheses"]),
            "scrubbed": bundle.pop("scrubbed_count", 0),
        }
        logger.info(
            f"📦 Knowledge export: {bundle['stats']['entities']} entities, "
            f"{bundle['stats']['relations']} relations, "
            f"{bundle['stats']['syntheses']} syntheses"
        )
        return bundle

    async def import_knowledge_bundle(
        self,
        bundle: dict,
        source_tag: str = "community_import",
        trust_floor: float = 0.5,
        dry_run: bool = False,
        kafka_publish_fn: Optional[Callable] = None,
    ) -> dict:
        """Import a knowledge bundle into the graph.

        Merges entities by name (no duplicates). Relations are added only if they
        don't already exist with higher trust. Imported relations get
        source='community_import' and capped trust_score.

        Args:
            bundle: JSON-LD bundle from export_knowledge_bundle
            source_tag: Source label for imported triples
            trust_floor: Max trust_score for imported relations (prevents
                         community data from outranking locally verified facts)
            dry_run: If True, only report what would be imported
        Returns: {entities_created, entities_skipped, relations_created,
                  relations_skipped, syntheses_created, errors}
        """
        stats = {
            "entities_created": 0, "entities_skipped": 0,
            "relations_created": 0, "relations_skipped": 0,
            "syntheses_created": 0, "errors": [],
        }
        now = datetime.now(timezone.utc).isoformat()

        entities = bundle.get("entities", [])
        relations = bundle.get("relations", [])
        syntheses = bundle.get("syntheses", [])

        async with self.driver.session() as session:
            # Import entities
            for ent in entities:
                name = ent.get("name", "").strip()
                if not name:
                    continue
                try:
                    if dry_run:
                        result = await session.run(
                            "MATCH (e:Entity {name: $name}) RETURN e.name AS n",
                            {"name": name},
                        )
                        exists = await result.single()
                        if exists:
                            stats["entities_skipped"] += 1
                        else:
                            stats["entities_created"] += 1
                        continue

                    result = await session.run(
                        """MERGE (e:Entity {name: $name})
                        ON CREATE SET
                            e.type = $type,
                            e.source = $source,
                            e.domain = $domain,
                            e.aliases = $aliases,
                            e.aliases_str = $aliases_str,
                            e.created = $now
                        RETURN e.source AS existing_source""",
                        {
                            "name": name,
                            "type": ent.get("type", "Concept"),
                            "source": source_tag,
                            "domain": ent.get("domain"),
                            "aliases": ent.get("aliases", []),
                            "aliases_str": " ".join(ent.get("aliases", [])),
                            "now": now,
                        },
                    )
                    record = await result.single()
                    if record and record["existing_source"] and record["existing_source"] != source_tag:
                        stats["entities_skipped"] += 1
                    else:
                        stats["entities_created"] += 1
                except Exception as e:
                    stats["errors"].append(f"Entity '{name}': {e}")

            # Import relations — with contradiction detection
            contradictions = []
            for rel in relations:
                subj = rel.get("subject", "").strip()
                obj = rel.get("object", "").strip()
                pred = rel.get("predicate", "").strip()
                if not (subj and obj and pred):
                    continue
                # Cap trust score
                import_trust = min(
                    float(rel.get("trust_score") or rel.get("confidence") or 0.5),
                    trust_floor,
                )

                # Contradiction detection: check if importing this triple
                # would contradict an existing high-trust local relation
                contra_rels = _CONTRADICTORY_PAIRS.get(pred, [])
                if contra_rels:
                    try:
                        for contra in contra_rels:
                            chk = await session.run(
                                f"""MATCH (s:Entity {{name: $s}})-[r:{contra}]->(o:Entity {{name: $o}})
                                WHERE r.trust_score >= $threshold
                                RETURN r.trust_score AS ts, r.source AS src""",
                                {"s": subj, "o": obj, "threshold": import_trust},
                            )
                            conflict = await chk.single()
                            if conflict:
                                contradictions.append({
                                    "imported": f"({subj})-[{pred}]->({obj})",
                                    "conflicts_with": f"({subj})-[{contra}]->({obj})",
                                    "existing_trust": conflict["ts"],
                                    "existing_source": conflict["src"],
                                })
                                stats["relations_skipped"] += 1
                                break
                        else:
                            # No contradiction found — proceed with import
                            pass
                        if contradictions and contradictions[-1]["imported"] == f"({subj})-[{pred}]->({obj})":
                            continue  # Skip this relation due to contradiction
                    except Exception:
                        pass  # Contradiction check failed — proceed with import

                try:
                    if dry_run:
                        result = await session.run(
                            f"""MATCH (s:Entity {{name: $s}})-[r:{pred}]->(o:Entity {{name: $o}})
                            RETURN r.trust_score AS ts""",
                            {"s": subj, "o": obj},
                        )
                        existing = await result.single()
                        if existing and (existing["ts"] or 0) >= import_trust:
                            stats["relations_skipped"] += 1
                        else:
                            stats["relations_created"] += 1
                        continue

                    # Only create if not exists or existing trust is lower
                    await session.run(
                        f"""MATCH (s:Entity {{name: $s}}), (o:Entity {{name: $o}})
                        MERGE (s)-[r:{pred}]->(o)
                        ON CREATE SET
                            r.source = $source,
                            r.confidence = $conf,
                            r.trust_score = $trust,
                            r.domain = $domain,
                            r.verified = false,
                            r.version = 1,
                            r.created = $now,
                            r.valid_from = $now
                        WITH r
                        WHERE r.trust_score < $trust
                        SET r.trust_score = $trust, r.source = $source""",
                        {
                            "s": subj, "o": obj,
                            "source": source_tag,
                            "conf": import_trust,
                            "trust": import_trust,
                            "domain": rel.get("domain"),
                            "now": now,
                        },
                    )
                    stats["relations_created"] += 1
                except Exception as e:
                    stats["errors"].append(f"Relation '{subj}-[{pred}]->{obj}': {e}")

            if contradictions:
                stats["contradictions"] = contradictions
                logger.warning(
                    f"⚠️ Import detected {len(contradictions)} contradictions "
                    f"with existing knowledge — skipped conflicting triples"
                )
                # Publish contradictions to moe.linting Kafka topic for admin review
                if kafka_publish_fn and not dry_run:
                    for c in contradictions:
                        try:
                            await kafka_publish_fn("moe.linting", {
                                "type": "import_contradiction",
                                "source": source_tag,
                                "imported_triple": c["imported"],
                                "conflicts_with": c["conflicts_with"],
                                "existing_trust": c.get("existing_trust"),
                                "existing_source": c.get("existing_source"),
                                "action": "skipped",
                                "timestamp": now,
                            })
                        except Exception:
                            pass

            # Import syntheses
            for syn in syntheses:
                text = syn.get("text", "").strip()
                if not text:
                    continue
                syn_id = hashlib.sha256(text.encode()).hexdigest()[:16]
                try:
                    if dry_run:
                        stats["syntheses_created"] += 1
                        continue
                    await session.run(
                        """MERGE (s:Synthesis {id: $id})
                        ON CREATE SET
                            s.text = $text,
                            s.insight_type = $type,
                            s.entities = $entities,
                            s.domain = $domain,
                            s.confidence = $conf,
                            s.source_model = $source,
                            s.created = $now""",
                        {
                            "id": syn_id,
                            "text": text[:500],
                            "type": syn.get("insight_type", "synthesis"),
                            "entities": syn.get("entities", []),
                            "domain": syn.get("domain"),
                            "conf": min(float(syn.get("confidence") or 0.5), trust_floor),
                            "source": source_tag,
                            "now": now,
                        },
                    )
                    stats["syntheses_created"] += 1
                except Exception as e:
                    stats["errors"].append(f"Synthesis: {e}")

        action = "DRY RUN" if dry_run else "IMPORT"
        logger.info(
            f"📥 Knowledge {action}: "
            f"{stats['entities_created']} entities, "
            f"{stats['relations_created']} relations, "
            f"{stats['syntheses_created']} syntheses "
            f"({len(stats['errors'])} errors)"
        )
        return stats

    # ─── KNOWLEDGE PROMOTION ─────────────────────────────────────────────────

    async def promote_knowledge(
        self,
        from_tenant_id: str,
        to_tenant_id: Optional[str],
        entity_names: Optional[list] = None,
    ) -> int:
        """Moves entities from one knowledge namespace to another.

        Used to promote user-private knowledge (user:{id}) up the hierarchy:
        user:{id} → team:{team_id} → tenant:{tenant_id} → None (global).

        Args:
            from_tenant_id: Source namespace (e.g. "user:abc123").
            to_tenant_id: Target namespace, or None for global (public).
            entity_names: Restrict promotion to specific entity names; None = all.

        Returns:
            Number of entities updated.
        """
        names_filter = "AND e.name IN $names" if entity_names else ""
        cypher = (
            "MATCH (e:Entity) "
            f"WHERE e.tenant_id = $from_tenant {names_filter} "
            "SET e.tenant_id = $to_tenant "
            "RETURN count(e) AS promoted"
        )
        async with self.driver.session() as session:
            result = await session.run(
                cypher,
                from_tenant=from_tenant_id,
                to_tenant=to_tenant_id,
                names=entity_names or [],
            )
            record = await result.single()
        count = record["promoted"] if record else 0
        logger.info(
            f"🔼 Knowledge promoted: {count} entities "
            f"{from_tenant_id!r} → {to_tenant_id!r}"
        )
        return count

    # ─── LIFECYCLE ───────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self.driver.close()
